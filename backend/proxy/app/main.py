"""用户 API Key 代理服务入口（端口 8004）。

职责：解密用户自备的第三方大模型 Key → 按厂商适配转发 → 统一 OpenAI 格式回传
（推理网关）→ 按价格表折算额度并落库。仅接受携带 X-Internal-Token 的内部调用。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from eduagent_shared import db
from eduagent_shared.config import get_settings
from eduagent_shared.logging import configure_logging, get_logger

from .errors import register_exception_handlers
from .routers import chat, usage

log = get_logger("proxy.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    db.init_engine(settings.database_url)
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=10.0),  # 长生成留出余量
        follow_redirects=False,
    )
    log.info("proxy_started")
    yield
    await app.state.http_client.aclose()
    await db.dispose_engine()


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="EduAgent API Proxy", version="0.1.0", lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(chat.router)
    app.include_router(usage.router)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
