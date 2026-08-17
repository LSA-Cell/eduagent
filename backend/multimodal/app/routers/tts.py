"""TTS 路由：文本合成语音，返回 WAV 字节流。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.deps import verify_internal_token
from app.providers import get_tts_provider
from app.schemas import TtsRequest

router = APIRouter(dependencies=[Depends(verify_internal_token)])


@router.post("/tts")
async def tts(payload: TtsRequest) -> Response:
    """合成语音，直接以 audio/wav 字节流返回。"""
    audio = await get_tts_provider().synthesize(
        payload.text, voice=payload.voice, speed=payload.speed
    )
    return Response(content=audio, media_type="audio/wav")
