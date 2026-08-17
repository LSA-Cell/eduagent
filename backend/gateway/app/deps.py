"""依赖装配：从 app.state 取运行期资源，便于测试注入 mock。"""

from __future__ import annotations

import httpx
import redis.asyncio as aioredis
from fastapi import Header, Request

from eduagent_shared.config import Settings, get_settings
from eduagent_shared.security.internal import InternalTokenError, verify_internal_token

from .errors import GatewayError
from .failover import FIRST_TOKEN_TIMEOUT_SECONDS, FailoverRouter
from .health import HealthRegistry


async def require_internal_token(
    x_internal_token: str | None = Header(default=None),
) -> None:
    """服务间令牌校验（仅 BFF 等内部服务可调）。"""
    try:
        verify_internal_token(x_internal_token)
    except InternalTokenError:
        raise GatewayError(
            403, "服务间令牌校验失败", "authentication_error", "invalid_internal_token"
        ) from None


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis


def get_registry(request: Request) -> HealthRegistry:
    return request.app.state.health_registry


def get_failover_router(request: Request) -> FailoverRouter:
    """每请求构造（无状态、廉价）；超时阈值允许测试通过 app.state 覆盖。"""
    return FailoverRouter(
        client=request.app.state.http_client,
        registry=request.app.state.health_registry,
        settings=get_settings(),
        first_token_timeout=getattr(
            request.app.state, "first_token_timeout", FIRST_TOKEN_TIMEOUT_SECONDS
        ),
    )
