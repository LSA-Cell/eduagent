"""聊天媒体直传：私有 MinIO 预签名、上传校验及下载链接。"""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from minio.error import S3Error
from pydantic import BaseModel, Field

from eduagent_shared.config import Settings
from eduagent_shared.logging import get_logger

from app import deps
from app.services import storage_service

router = APIRouter(tags=["uploads"], dependencies=[Depends(deps.require_internal_auth)])
log = get_logger("agent.routers.uploads")

MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_REPORT_SIZE = 5 * 1024 * 1024
POST_FORM_OVERHEAD = 16 * 1024
EXPIRES = storage_service.DEFAULT_PRESIGNED_EXPIRES
MEDIA_TYPES = {
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "png": {"image/png"},
    "webp": {"image/webp"},
    "m4a": {"audio/mp4", "audio/m4a", "audio/x-m4a"},
    "wav": {"audio/wav", "audio/x-wav", "audio/wave"},
    "mp3": {"audio/mpeg", "audio/mp3"},
    "ogg": {"audio/ogg"},
    "csv": {"text/csv", "application/vnd.ms-excel"},
    "xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    "pdf": {"application/pdf"},
}
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
AUDIO_EXTENSIONS = {"m4a", "wav", "mp3", "ogg"}
REPORT_EXTENSIONS = {"csv", "xlsx"}
Purpose = Literal["chat_image", "chat_audio", "report_file", "certification_file"]


class PresignRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    content_type: str
    size_bytes: int = Field(gt=0, le=MAX_FILE_SIZE)
    purpose: Purpose


class ConfirmRequest(BaseModel):
    object_key: str = Field(min_length=1, max_length=512)
    purpose: Purpose


def _validate_media(extension: str, content_type: str, purpose: Purpose) -> None:
    if extension not in MEDIA_TYPES or content_type not in MEDIA_TYPES[extension]:
        raise HTTPException(status_code=400, detail="unsupported media type")
    if purpose not in _purposes_for_extension(extension):
        raise HTTPException(status_code=400, detail="media type does not match purpose")


def _purposes_for_extension(extension: str) -> set[Purpose]:
    if extension in IMAGE_EXTENSIONS:
        if extension in {"jpg", "png"}:
            return {"chat_image", "certification_file"}
        return {"chat_image"}
    if extension in AUDIO_EXTENSIONS:
        return {"chat_audio"}
    if extension in REPORT_EXTENSIONS:
        return {"report_file"}
    return {"certification_file"}


def _owned_extension(object_key: str, user_id: str) -> str:
    # 固定三段路径，禁止跨用户、额外目录与路径穿越。
    match = re.fullmatch(rf"u/{re.escape(user_id)}/[0-9a-f]{{32}}\.([a-z0-9]+)", object_key)
    if match is None or match[1] not in MEDIA_TYPES:
        raise HTTPException(status_code=404, detail="file not found")
    return match[1]


async def _storage_call(function, *args, **kwargs):
    """MinIO SDK 为同步接口，在线程中调用并隐藏内部异常详情。"""
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
            raise HTTPException(status_code=404, detail="file not found") from exc
        log.warning("media_storage_failed", error_type=type(exc).__name__)
        raise HTTPException(status_code=503, detail="file storage unavailable") from exc
    except Exception as exc:  # noqa: BLE001 - 对象存储边界不透出地址、凭据或原始错误
        log.warning("media_storage_failed", error_type=type(exc).__name__)
        raise HTTPException(status_code=503, detail="file storage unavailable") from exc


async def _verified_object(object_key: str, extension: str, settings: Settings) -> None:
    info = await _storage_call(storage_service.stat_object, object_key, settings=settings)
    max_size = MAX_REPORT_SIZE if extension in REPORT_EXTENSIONS else MAX_FILE_SIZE
    if not 0 < info.size <= max_size:
        raise HTTPException(status_code=400, detail="file size is out of range")
    content_type = (info.content_type or "").split(";", 1)[0].strip().lower()
    if content_type not in MEDIA_TYPES[extension]:
        raise HTTPException(status_code=400, detail="unsupported media type")


@router.post("/uploads/presign")
async def presign_upload(
    req: PresignRequest,
    user_id: str = Depends(deps.get_user_id),
    settings: Settings = Depends(deps.get_settings),
) -> dict[str, Any]:
    extension = req.file_name.rsplit(".", 1)[-1].lower() if "." in req.file_name else ""
    content_type = req.content_type.strip().lower()
    _validate_media(extension, content_type, req.purpose)
    max_size = MAX_REPORT_SIZE if req.purpose == "report_file" else MAX_FILE_SIZE
    if req.size_bytes > max_size:
        raise HTTPException(status_code=400, detail="file size is out of range")
    object_key = f"u/{user_id}/{uuid.uuid4().hex}.{extension}"
    upload_url, fields = await _storage_call(
        storage_service.create_presigned_post,
        object_key, content_type, settings=settings,
        max_size=max_size + POST_FORM_OVERHEAD, expires=EXPIRES,
    )
    return {
        "upload_url": upload_url,
        "object_key": object_key,
        "method": "POST",
        "expires_in": EXPIRES,
        "fields": fields,
    }


@router.post("/uploads/confirm")
async def confirm_upload(
    req: ConfirmRequest,
    user_id: str = Depends(deps.get_user_id),
    settings: Settings = Depends(deps.get_settings),
) -> dict[str, Any]:
    extension = _owned_extension(req.object_key, user_id)
    if req.purpose not in _purposes_for_extension(extension):
        raise HTTPException(status_code=400, detail="media type does not match purpose")
    await _verified_object(req.object_key, extension, settings)
    download_url = await _storage_call(
        storage_service.presigned_get, req.object_key, settings=settings, expires=EXPIRES
    )
    return {
        "object_key": req.object_key,
        "verified": True,
        "download_url": download_url,
        "file_url": download_url,
        "document_id": None,
        "status": "ready",
        "expires_in": EXPIRES,
    }


@router.get("/files/{object_key:path}/url")
async def file_url(
    object_key: str,
    user_id: str = Depends(deps.get_user_id),
    settings: Settings = Depends(deps.get_settings),
) -> dict[str, Any]:
    extension = _owned_extension(object_key, user_id)
    await _verified_object(object_key, extension, settings)
    download_url = await _storage_call(
        storage_service.presigned_get, object_key, settings=settings, expires=EXPIRES
    )
    return {"download_url": download_url, "expires_in": EXPIRES}
