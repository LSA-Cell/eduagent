"""中间件：请求日志、限流。"""

from __future__ import annotations

import time
import uuid

import structlog
import structlog.contextvars
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from eduagent_shared.security.jwt import TokenError, decode_token

from .errors import error_body

log = structlog.get_logger("bff.middleware")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """记录请求方法/路径/状态码/耗时，并透传 X-Request-ID。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id, path=request.url.path, method=request.method
        )
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        log.info("request_done", status_code=response.status_code, duration_ms=duration_ms)
        response.headers["X-Request-ID"] = request_id
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """全局限流中间件：免费 20 / 付费 60 / 教师 100 RPM（app.state.rate_limits 可配）。

    身份优先级：Bearer token 中的 user_id > 客户端 IP。
    Redis 不可用时由 SlidingWindowLimiter 内部 fail-open。
    """

    _SKIP_PREFIXES = ("/healthz", "/docs", "/redoc", "/openapi.json", "/ws")

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "OPTIONS" or request.url.path.startswith(self._SKIP_PREFIXES):
            return await call_next(request)

        app = request.app
        limiter = getattr(app.state, "rate_limiter", None)
        limits = getattr(app.state, "rate_limits", None)
        if limiter is None or limits is None:
            return await call_next(request)

        identity, user_id = self._identity(request)
        tier = "free"
        if user_id:
            resolver = getattr(app.state, "rate_tier_resolver", None)
            if resolver is not None:
                try:
                    tier = await resolver(user_id)
                except Exception:
                    tier = "free"
        limit = int(limits.get(tier, limits["free"]))

        if not await limiter.allow(identity, limit):
            return JSONResponse(
                status_code=429,
                content=error_body(
                    "rate_limited",
                    "请求过于频繁，请稍后再试",
                    {"limit_per_minute": limit, "tier": tier},
                ),
                headers={"Retry-After": "60"},
            )
        return await call_next(request)

    @staticmethod
    def _identity(request: Request) -> tuple[str, str | None]:
        """从 Bearer token 解析用户 ID；失败回退 IP。"""
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            try:
                claims = decode_token(token, expected_type="access")
                sub = claims.get("sub")
                if sub:
                    return f"u:{sub}", sub
            except TokenError:
                pass
        host = request.client.host if request.client else "unknown"
        return f"ip:{host}", None
