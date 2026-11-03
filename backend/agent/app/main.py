"""Agent 编排服务入口（端口 8002）。

只对 BFF 暴露：业务路由统一要求 X-Internal-Token；/health 供 compose 健康检查（无鉴权）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from eduagent_shared import db
from eduagent_shared.config import get_settings
from eduagent_shared.logging import configure_logging, get_logger

from app.routers import analytics, chat, flashcards, kb, mistakes, plans, teacher, uploads, voice

log = get_logger("agent.main")

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """启动：初始化 DB 引擎（惰性连接）；关停：释放连接池。"""
    settings = get_settings()
    db.init_engine(settings.database_url)
    log.info("agent_started")
    yield
    await db.dispose_engine()


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="EduAgent Agent 编排服务", version="0.1.0", lifespan=lifespan)

    app.include_router(chat.router)
    app.include_router(mistakes.router)
    app.include_router(flashcards.router)
    app.include_router(plans.router)
    app.include_router(teacher.router)
    app.include_router(analytics.router)
    app.include_router(voice.router)
    app.include_router(kb.router)
    app.include_router(uploads.router)

    # Wave B：编排层新端点（/v1/chat/stream、/v1/skills/toolbar、/v1/guardrails/confirms）
    from app.api import chat_stream, confirms, skills

    app.include_router(chat_stream.router)
    app.include_router(skills.router)
    app.include_router(confirms.router)

    # Wave C：交互协作机制端点（ask 答复回传 / plan 确认协议 / 运行中消息与停止）
    from app.api import ask as ask_api
    from app.api import inbox as inbox_api
    from app.api import plan_mode

    app.include_router(ask_api.router)
    app.include_router(plan_mode.router)
    app.include_router(inbox_api.router)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
