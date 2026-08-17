"""生产 Provider 骨架：PaddleOCR / Whisper / 真实 TTS。

这些类给出了与 mock 完全一致的接口，真实依赖（paddleocr、faster-whisper 等）
体积大且需 GPU/模型文件，仅在生产镜像中按需安装并通过配置启用。
未安装依赖时实例化会抛出明确的 ImportError，提示切换回 mock。
"""

from __future__ import annotations

from app.providers.base import AsrProvider, AsrResult, OcrProvider, OcrResult, TtsProvider


class PaddleOcrProvider(OcrProvider):
    """PaddleOCR 实现骨架（数学公式走 PP-FormulaNet 后处理为 LaTeX）。"""

    def __init__(self, lang: str = "ch", use_formula: bool = True) -> None:
        try:
            from paddleocr import PaddleOCR  # type: ignore
        except ImportError as exc:  # pragma: no cover - 生产环境才有依赖
            raise ImportError(
                "paddleocr 未安装，请将 OCR_PROVIDER 设为 mock 或安装 paddleocr"
            ) from exc
        self._ocr = PaddleOCR(use_angle_cls=True, lang=lang)
        self._use_formula = use_formula

    async def recognize(self, image_bytes: bytes) -> OcrResult:  # pragma: no cover
        import numpy as np
        from PIL import Image
        import io as _io

        img = Image.open(_io.BytesIO(image_bytes)).convert("RGB")
        result = self._ocr.ocr(np.array(img), cls=True)
        boxes: list[dict] = []
        texts: list[str] = []
        for line in result[0] or []:
            points, (txt, _conf) = line
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            boxes.append(
                {
                    "x": int(min(xs)),
                    "y": int(min(ys)),
                    "w": int(max(xs) - min(xs)),
                    "h": int(max(ys) - min(ys)),
                    "text": txt,
                }
            )
            texts.append(txt)
        # 公式区域由公式检测模型给出 LaTeX，骨架阶段留空
        return OcrResult(text="\n".join(texts), latex_blocks=[], boxes=boxes)


class WhisperAsrProvider(AsrProvider):
    """faster-whisper 实现骨架。"""

    def __init__(self, model_size: str = "small") -> None:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "faster-whisper 未安装，请将 ASR_PROVIDER 设为 mock 或安装 faster-whisper"
            ) from exc
        self._model = WhisperModel(model_size)

    async def transcribe(self, audio_bytes: bytes, language: str | None = None) -> AsrResult:  # pragma: no cover
        import io as _io

        segments_iter, info = self._model.transcribe(_io.BytesIO(audio_bytes), language=language)
        segments = [
            {"start": seg.start, "end": seg.end, "text": seg.text.strip()}
            for seg in segments_iter
        ]
        text = "".join(seg["text"] for seg in segments)
        return AsrResult(text=text, segments=segments, language=info.language)


class EdgeTtsProvider(TtsProvider):
    """edge-tts 实现骨架（云端音色，无需本地模型）。"""

    def __init__(self) -> None:
        try:
            import edge_tts  # type: ignore  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "edge-tts 未安装，请将 TTS_PROVIDER 设为 mock 或安装 edge-tts"
            ) from exc

    async def synthesize(self, text: str, voice: str = "default", speed: float = 1.0) -> bytes:  # pragma: no cover
        import edge_tts

        voice_map = {"default": "zh-CN-XiaoxiaoNeural"}
        rate = f"{int((speed - 1.0) * 100):+d}%"
        communicate = edge_tts.Communicate(text, voice_map.get(voice, voice), rate=rate)
        audio = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio += chunk["data"]
        return audio
