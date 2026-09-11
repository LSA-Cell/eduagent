"""Billing 服务入口（端口 8006）。

职责：会员套餐 / 订阅状态 / 订单 / 支付渠道 webhook（docs/billing/spec.md）。
内部服务：仅 net_app 网络可达；除 /billing/webhooks/* 外一律要求 X-Internal-Token。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from eduagent_shared import db
from eduagent_shared.config import get_settings
from eduagent_shared.logging import configure_logging, get_logger

from .deps import require_internal_token
from .errors import register_exception_handlers
from .routers import admin, orders, plans, subscription, webhooks

log = get_logger("billing.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    db.init_engine(settings.database_url)
    log.info("billing_started")
    yield
    await db.dispose_engine()


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="EduAgent Billing", version="0.1.0", lifespan=lifespan)
    register_exception_handlers(app)

    internal = [Depends(require_internal_token)]
    app.include_router(plans.router, dependencies=internal)
    app.include_router(subscription.router, dependencies=internal)
    app.include_router(orders.router, dependencies=internal)
    app.include_router(admin.router)  # 路由级已挂 internal token 依赖
    # webhook：公网可达（nginx /webhooks/billing/），无 internal token，靠渠道 HMAC 验签
    app.include_router(webhooks.router)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
