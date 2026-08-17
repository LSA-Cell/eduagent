"""文档生成服务主入口：结构化 JSON -> PPTX/DOCX/PDF -> MinIO（端口 8005）。

服务边界：只做排版渲染与对象存储，不做内容生成，不访问业务数据库。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.config import get_settings
from app.routers import render, tasks


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例。"""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(title="EduAgent DocGen Service", version="0.1.0")

    @app.get("/health", include_in_schema=False)
    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """健康检查（无需内部令牌）。"""
        return {"status": "ok", "service": "docgen"}

    app.include_router(render.router)
    app.include_router(tasks.router)
    return app


app = create_app()
