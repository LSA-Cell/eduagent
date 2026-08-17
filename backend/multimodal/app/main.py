"""多模态服务主入口：ASR/OCR/TTS/文档解析/图片预处理（端口 8003）。

服务边界：仅做多模态处理，不含学科业务逻辑，不写业务数据库。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.config import get_settings
from app.routers import asr, grade, ocr, parse, tts


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例。"""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(title="EduAgent Multimodal Service", version="0.1.0")

    @app.get("/health", include_in_schema=False)
    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """健康检查（无需内部令牌）。"""
        return {"status": "ok", "service": "multimodal"}

    for r in (ocr.router, asr.router, tts.router, parse.router, grade.router):
        app.include_router(r)
    return app


app = create_app()
