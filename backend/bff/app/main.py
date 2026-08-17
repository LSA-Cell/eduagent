"""BFF 应用入口（端口 8000）。"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from eduagent_shared import db, redis_client
from eduagent_shared.config import get_settings
from eduagent_shared.logging import configure_logging, get_logger

from .errors import register_exception_handlers
from .middleware import RateLimitMiddleware, RequestLoggingMiddleware
from .ratelimit import RateTierResolver, SlidingWindowLimiter
from .routers import api_keys, auth, business, chat, models, users
from .ws import router as ws_router

log = get_logger("bff.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    db.init_engine(settings.database_url)
    app.state.redis = redis_client.create_redis()
    app.state.rate_limiter = SlidingWindowLimiter(app.state.redis)
    app.state.rate_limits = {
        "free": settings.rate_limit_free,
        "paid": settings.rate_limit_paid,
        "teacher": settings.rate_limit_teacher,
    }
    app.state.rate_tier_resolver = RateTierResolver(
        app.state.redis, db.get_session_factory()
    )
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=10.0),
        follow_redirects=False,
    )
    log.info("bff_started")
    yield
    await app.state.http_client.aclose()
    await app.state.redis.aclose()
    await db.dispose_engine()


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="EduAgent BFF", version="0.1.0", lifespan=lifespan)

    register_exception_handlers(app)
    # 注意顺序：后添加的包裹外层 → 日志最外层，其次 CORS，限流最内
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 移动端无浏览器跨域限制，dev 全放开；生产收敛到 App 域名
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)

    # 业务路由统一挂在 /api/v1 前缀下（对齐 docs/api/openapi.md 的 Base URL 与移动端
    # AppConfig.apiPrefix；nginx 按原路径透传，不重写 URI）。ws 与 health 保持无前缀。
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(users.router, prefix="/api/v1")
    app.include_router(api_keys.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(models.router, prefix="/api/v1")
    app.include_router(business.router, prefix="/api/v1")
    app.include_router(ws_router)

    @app.get("/health", include_in_schema=False)
    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
