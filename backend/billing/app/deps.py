"""依赖装配：内部令牌校验 / 用户身份头 / 仓储（app.state 取资源，便于测试注入）。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from eduagent_shared.db import get_session
from eduagent_shared.security.internal import InternalTokenError, verify_internal_token

from .errors import BillingError
from .services.store import SqlAlchemyBillingStore


async def require_internal_token(
    x_internal_token: str | None = Header(default=None),
) -> None:
    """服务间令牌校验（仅 BFF 等内部服务可调；webhook 路由除外，靠渠道签名验真）。"""
    try:
        verify_internal_token(x_internal_token)
    except InternalTokenError:
        raise BillingError(
            403, "服务间令牌校验失败", "authentication_error", "invalid_internal_token"
        ) from None


async def require_user_id(
    x_user_id: str | None = Header(default=None),
) -> uuid.UUID:
    """用户身份由 BFF 验完 JWT 后经 X-User-Id 注入；直连缺头 = 401。"""
    if not x_user_id:
        raise BillingError(401, "缺少用户身份", "authentication_error", "missing_user_id")
    try:
        return uuid.UUID(x_user_id)
    except ValueError:
        raise BillingError(
            401, "用户身份不合法", "authentication_error", "invalid_user_id"
        ) from None


async def get_billing_store(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlAlchemyBillingStore:
    return SqlAlchemyBillingStore(session)
