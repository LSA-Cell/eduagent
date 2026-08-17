"""ASR 路由：音频转写。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.deps import verify_internal_token
from app.providers import get_asr_provider
from app.routers.common import resolve_bytes
from app.schemas import AsrResponse, AsrSegment

router = APIRouter(dependencies=[Depends(verify_internal_token)])


@router.post("/asr", response_model=AsrResponse)
async def asr(
    file: Annotated[UploadFile | None, File()] = None,
    audio_url: Annotated[str | None, Form()] = None,
    language: Annotated[str | None, Form()] = None,
) -> AsrResponse:
    """转写音频为带时间戳的分段文本，支持 multipart 或 audio_url。"""
    raw = await resolve_bytes(file, audio_url, "音频")
    result = await get_asr_provider().transcribe(raw, language=language)
    return AsrResponse(
        text=result.text,
        segments=[AsrSegment(**s) for s in result.segments],
        language=result.language,
    )
