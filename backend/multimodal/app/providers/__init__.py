"""Provider 工厂：按配置返回 OCR/ASR/TTS 实现（默认 mock）。"""

from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.providers.base import AsrProvider, OcrProvider, TtsProvider
from app.providers.mock import MockAsrProvider, MockOcrProvider, MockTtsProvider


@lru_cache
def get_ocr_provider() -> OcrProvider:
    """按 OCR_PROVIDER 配置创建 OCR Provider。"""
    name = get_settings().ocr_provider.lower()
    if name == "paddleocr":
        from app.providers.real import PaddleOcrProvider

        return PaddleOcrProvider()
    return MockOcrProvider()


@lru_cache
def get_asr_provider() -> AsrProvider:
    """按 ASR_PROVIDER 配置创建 ASR Provider。"""
    name = get_settings().asr_provider.lower()
    if name == "whisper":
        from app.providers.real import WhisperAsrProvider

        return WhisperAsrProvider()
    return MockAsrProvider()


@lru_cache
def get_tts_provider() -> TtsProvider:
    """按 TTS_PROVIDER 配置创建 TTS Provider。"""
    name = get_settings().tts_provider.lower()
    if name == "edge":
        from app.providers.real import EdgeTtsProvider

        return EdgeTtsProvider()
    return MockTtsProvider()
