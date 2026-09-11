"""依赖装配：内部令牌校验 / HTTP 客户端 / Key 仓储（从 app.state 取运行期资源，便于测试注入 mock）。"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from eduagent_shared.db import get_session
from eduagent_shared.security.internal import InternalTokenError, verify_internal_token

from .errors import ProxyError
from .services.quota import SqlAlchemyQuotaStore
from .services.store import SqlAlchemyApiKeyStore


async def require_internal_token(
    x_internal_token: str | None = Header(default=None),
) -> None:
    """服务间令牌校验（仅推理网关等内部服务可调）。"""
    try:
        verify_internal_token(x_internal_token)
    except InternalTokenError:
        raise ProxyError(
            403, "服务间令牌校验失败", "authentication_error", "invalid_internal_token"
        ) from None


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


async def get_key_store(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlAlchemyApiKeyStore:
    return SqlAlchemyApiKeyStore(session)


async def get_quota_store(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlAlchemyQuotaStore:
    return SqlAlchemyQuotaStore(session)
