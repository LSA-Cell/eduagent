"""FastAPI 依赖：配置 / Redis / HTTP 客户端 / 仓储 / 当前用户。"""

from __future__ import annotations

import uuid
from typing import Annotated

import httpx
import redis.asyncio as aioredis
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from eduagent_shared.config import Settings, get_settings
from eduagent_shared.db import get_session
from eduagent_shared.security.jwt import TokenError, decode_token

from .errors import AppError
from .services import devices
from .services.store import (
    SqlAlchemyApiKeyStore,
    SqlAlchemyChatStore,
    SqlAlchemyUserStore,
)

_bearer = HTTPBearer(auto_error=False)


def get_settings_dep() -> Settings:
    return get_settings()


def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


async def get_user_store(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlAlchemyUserStore:
    return SqlAlchemyUserStore(session)


async def get_api_key_store(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlAlchemyApiKeyStore:
    return SqlAlchemyApiKeyStore(session)


async def get_chat_store(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlAlchemyChatStore:
    return SqlAlchemyChatStore(session)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    user_store: Annotated[SqlAlchemyUserStore, Depends(get_user_store)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
):
    """解析 Bearer access token 并加载用户；失败统一 401。

    登出/踢下线会把 access jti 写入 Redis 黑名单（TTL 至自然过期），此处校验；
    Redis 故障 fail-open（与限流器同一可用性策略）。
    """
    if credentials is None:
        raise AppError(401, "unauthorized", "缺少访问令牌")
    try:
        claims = decode_token(credentials.credentials, expected_type="access")
        user_id = uuid.UUID(str(claims["sub"]))
    except (TokenError, KeyError, ValueError):
        raise AppError(401, "unauthorized", "访问令牌无效或已过期") from None
    jti = claims.get("jti")
    if jti and await devices.is_access_blacklisted(redis, str(jti)):
        raise AppError(401, "unauthorized", "登录状态已失效，请重新登录")
    user = await user_store.get_by_id(user_id)
    if user is None:
        raise AppError(401, "unauthorized", "用户不存在或已被删除")
    return user


async def require_teacher(user=Depends(get_current_user)):
    """教师身份校验（/teacher/* 资源使用）。"""
    if not user.is_teacher:
        raise AppError(403, "forbidden", "该功能仅教师身份可用")
    return user
