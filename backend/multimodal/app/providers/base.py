"""Provider 抽象：OCR / ASR / TTS 三套可插拔接口。

默认使用 mock 实现以便离线运行；生产实现（PaddleOCR、Whisper、TTS 引擎）
以骨架形式提供，部署时通过配置切换，业务代码不感知差异。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class OcrResult:
    """OCR 识别结果。"""

    text: str
    latex_blocks: list[str] = field(default_factory=list)
    boxes: list[dict] = field(default_factory=list)  # {x, y, w, h, text}


@dataclass
class AsrResult:
    """语音识别结果。"""

    text: str
    segments: list[dict] = field(default_factory=list)  # {start, end, text}
    language: str = "zh"


class OcrProvider(ABC):
    """OCR 提供者接口。输入为预处理后的图片字节。"""

    @abstractmethod
    async def recognize(self, image_bytes: bytes) -> OcrResult:
        """识别图片中的文字与公式。"""
        raise NotImplementedError


class AsrProvider(ABC):
    """ASR 提供者接口。输入为音频字节。"""

    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, language: str | None = None) -> AsrResult:
        """转写音频为带时间戳的文本段。"""
        raise NotImplementedError


class TtsProvider(ABC):
    """TTS 提供者接口。输出为 WAV 音频字节。"""

    @abstractmethod
    async def synthesize(self, text: str, voice: str = "default", speed: float = 1.0) -> bytes:
        """合成语音，返回 WAV 字节流。"""
        raise NotImplementedError
