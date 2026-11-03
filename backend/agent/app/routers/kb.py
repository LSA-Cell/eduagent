"""知识库文档路由：预签名上传 → 确认解析 → 列表 / 详情 / 删除。

文件流不经本服务：客户端凭预签名 URL 直传 MinIO；confirm 后由 Celery 任务
（agent.kb.parse_document）异步解析：rag_service 分块 + embedding → kb_chunks。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eduagent_shared.config import Settings
from eduagent_shared.logging import get_logger
from eduagent_shared.models import KbChunk, KnowledgeDocument

from app import deps
from app.schemas import DocumentUploadUrlRequest
from app.services import storage_service
from app.tasks import kb_tasks

log = get_logger("agent.routers.kb")

router = APIRouter(
    prefix="/kb/documents",
    tags=["kb"],
    dependencies=[Depends(deps.require_internal_auth)],
)

ALLOWED_EXTENSIONS = {"pdf", "docx", "txt", "md"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
UPLOAD_URL_EXPIRES = storage_service.DEFAULT_PRESIGNED_EXPIRES


def document_to_dict(document: Any) -> dict[str, Any]:
    """KnowledgeDocument → API DTO。"""
    return {
        "id": str(document.id),
        "title": document.title,
        "file_name": document.file_name,
        "file_type": document.file_type,
        "object_key": document.object_key,
        "size_bytes": document.size_bytes,
        "subject": document.subject,
        "status": document.status,
        "chunk_count": document.chunk_count,
        "created_at": _iso(document.created_at),
    }


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _validate_file(filename: str, size: Optional[int]) -> str:
    """校验扩展名与大小上限，返回小写扩展名。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {ext or '（无扩展名）'}，仅支持 pdf/docx/txt/md",
        )
    if size is not None and size > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="文件超过 50MB 大小上限")
    return ext


async def _get_owned(db: AsyncSession, document_id: str, user_id: str) -> Any:
    """取文档并校验归属，不存在/非本人一律 404。"""
    document = await db.get(KnowledgeDocument, document_id)
    if document is None or str(document.user_id) != str(user_id):
        raise HTTPException(status_code=404, detail="document not found")
    return document


@router.post("/upload-url", status_code=201)
async def create_upload_url(
    req: DocumentUploadUrlRequest,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
    settings: Settings = Depends(deps.get_settings),
) -> dict[str, Any]:
    """预签名上传：校验类型/大小 → 预建文档行（uploading）→ 返回 PUT 预签名 URL。"""
    ext = _validate_file(req.filename, req.size)
    object_key = f"kb/{user_id}/{uuid.uuid4()}.{ext}"
    document = KnowledgeDocument(
        user_id=user_id,
        title=req.filename,
        file_name=req.filename,
        file_type=ext,
        object_key=object_key,
        size_bytes=req.size,
        status="uploading",
    )
    db.add(document)
    await db.flush()
    await db.commit()
    upload_url = await asyncio.to_thread(
        storage_service.create_presigned_put,
        object_key, settings=settings, expires=UPLOAD_URL_EXPIRES,
    )
    file_url = await asyncio.to_thread(
        storage_service.presigned_get,
        object_key, settings=settings, expires=UPLOAD_URL_EXPIRES,
    )
    log.info("kb_upload_url_created", user_id=user_id, document_id=str(document.id))
    return {
        "document_id": str(document.id),
        "upload_url": upload_url,
        "file_url": file_url,
        "object_key": object_key,
        "expires_in": UPLOAD_URL_EXPIRES,
    }


@router.post("/{document_id}/confirm")
async def confirm_upload(
    document_id: str,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """上传完成确认：status=processing 并派发解析任务。"""
    document = await _get_owned(db, document_id, user_id)
    if document.status != "uploading":
        raise HTTPException(status_code=409, detail=f"当前状态不可确认: {document.status}")
    document.status = "processing"
    await db.commit()
    task = kb_tasks.parse_document.delay(str(document.id)) # type: ignore
    log.info("kb_parse_dispatched", document_id=document_id, task_id=getattr(task, "id", None))
    return {
        "document_id": str(document.id),
        "status": "processing",
        "task_id": getattr(task, "id", None),
    }


@router.get("")
async def list_documents(
    status: Optional[str] = Query(default=None, description="状态过滤"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """文档列表：按创建时间倒序分页。"""
    stmt = (
        select(KnowledgeDocument)
        .where(KnowledgeDocument.user_id == user_id)
        .order_by(KnowledgeDocument.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        stmt = stmt.where(KnowledgeDocument.status == status)
    documents = (await db.execute(stmt)).scalars().all()
    return {"items": [document_to_dict(d) for d in documents]}


@router.get("/{document_id}")
async def get_document(
    document_id: str,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """文档详情。"""
    return document_to_dict(await _get_owned(db, document_id, user_id))


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
    settings: Settings = Depends(deps.get_settings),
) -> dict[str, Any]:
    """删除文档：连同 kb_chunks 与 MinIO 原始对象一起清理。"""
    document = await _get_owned(db, document_id, user_id)
    chunks = (
        await db.execute(select(KbChunk).where(KbChunk.document_id == document.id))
    ).scalars().all()
    for chunk in chunks:
        await db.delete(chunk)
    await db.delete(document)
    await db.commit()
    if document.object_key:
        try:
            storage_service.remove_object(document.object_key, settings=settings)
        except Exception as exc:  # noqa: BLE001 - 存储对象清理失败不阻断删除结果
            log.warning("kb_object_remove_failed", object_key=document.object_key, error=str(exc))
    log.info("kb_document_deleted", user_id=user_id, document_id=document_id)
    return {"deleted": True, "document_id": document_id}
