"""推理网关应用入口（端口 8001）。"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from eduagent_shared import redis_client
from eduagent_shared.config import get_settings
from eduagent_shared.logging import configure_logging, get_logger

from .errors import register_exception_handlers
from .health import HealthRegistry
from .routes import router

log = get_logger("gateway.main")

# 模型卡片配置文件（默认取 gateway/model_cards.json，可用 env 覆盖）
MODEL_CARDS_PATH = Path(
    os.environ.get(
        "MODEL_CARDS_PATH", Path(__file__).resolve().parents[1] / "model_cards.json"
    )
)


def load_model_cards(path: Path = MODEL_CARDS_PATH) -> list[dict]:
    """加载模型卡片；文件缺失/损坏时降级为空列表并告警。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("model_cards_load_error", path=str(path), error=str(exc))
        return []


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.redis = redis_client.create_redis()
    app.state.health_registry = HealthRegistry(
        app.state.redis, ttl_seconds=settings.model_health_ttl_seconds
    )
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=5.0), follow_redirects=False
    )
    app.state.model_cards = load_model_cards()
    log.info(
        "gateway_started",
        vllm_models=list(settings.vllm_routes.keys()),
        failover_enabled=settings.failover_enabled,
    )
    yield
    await app.state.http_client.aclose()
    await app.state.redis.aclose()


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="EduAgent Inference Gateway", version="0.1.0", lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(router)

    @app.get("/health", include_in_schema=False)
    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
