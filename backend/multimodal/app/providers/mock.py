"""Mock Provider：离线可运行的确定性实现，供开发与测试使用。"""

from __future__ import annotations

import hashlib
import io
import math
import struct
import wave

from app.providers.base import AsrProvider, AsrResult, OcrProvider, OcrResult, TtsProvider


class MockOcrProvider(OcrProvider):
    """确定性 OCR：基于图片字节哈希生成稳定的文本/公式/坐标框。

    输出布局模拟一页作业：若干题目行 + 一条数学公式（LaTeX）。
    """

    async def recognize(self, image_bytes: bytes) -> OcrResult:
        seed = int.from_bytes(hashlib.sha256(image_bytes).digest()[:4], "big")
        boxes: list[dict] = []
        lines = ["1. 3+5=8", "2. 7x=21, x=3", "3. 小明有 12 个苹果"]
        y = 40
        for line in lines:
            boxes.append(
                {
                    "x": 32,
                    "y": y,
                    "w": 8 * len(line) + 160 + (seed % 17),
                    "h": 28,
                    "text": line,
                }
            )
            y += 48
        latex_blocks = [r"\frac{3}{4} + \frac{1}{4} = 1"]
        boxes.append({"x": 32, "y": y, "w": 220, "h": 40, "text": "3/4 + 1/4 = 1"})
        text = "\n".join(lines + ["3/4 + 1/4 = 1"])
        return OcrResult(text=text, latex_blocks=latex_blocks, boxes=boxes)


class MockAsrProvider(AsrProvider):
    """确定性 ASR：返回固定文本与均匀分段。"""

    async def transcribe(self, audio_bytes: bytes, language: str | None = None) -> AsrResult:
        # 依据字节数估算时长，至少 1 秒
        duration = max(1.0, len(audio_bytes) / 16000.0)
        text = "同学们好，今天我们学习分数的加法。"
        half = duration / 2
        segments = [
            {"start": 0.0, "end": round(half, 3), "text": "同学们好，"},
            {"start": round(half, 3), "end": round(duration, 3), "text": "今天我们学习分数的加法。"},
        ]
        return AsrResult(text=text, segments=segments, language=language or "zh")


class MockTtsProvider(TtsProvider):
    """Mock TTS：生成与文本长度成正比的静音 WAV（16kHz 单声道 16bit）。"""

    SAMPLE_RATE = 16000

    async def synthesize(self, text: str, voice: str = "default", speed: float = 1.0) -> bytes:
        # 每个字符约 0.15 秒，语速越快时长越短
        duration = max(0.2, 0.15 * len(text) / speed)
        frames = int(self.SAMPLE_RATE * duration)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.SAMPLE_RATE)
            wf.writeframes(struct.pack("<" + "h" * frames, *([0] * frames)))
        return buf.getvalue()
