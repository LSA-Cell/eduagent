"""内部服务间鉴权依赖。

共享包 eduagent_shared.security.internal.verify_internal_token 是纯函数
（失败抛 InternalTokenError），这里包装为 FastAPI 依赖；
共享包未安装时使用本地等价校验（X-Internal-Token 常量时间比对）。
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.config import get_settings

try:  # pragma: no cover - 取决于运行环境是否安装了共享包
    from eduagent_shared.security.internal import (
        InternalTokenError,
        verify_internal_token as _shared_verify,
    )

    _HAS_SHARED = True
except ImportError:
    _HAS_SHARED = False


async def verify_internal_token(
    x_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    """校验调用方内部服务令牌，失败返回 401。

    约定：网关注入 ``X-Internal-Token`` 头，值与 INTERNAL_SERVICE_TOKEN 一致。
    """
    expected = get_settings().internal_service_token
    if _HAS_SHARED:
        try:
            _shared_verify(x_internal_token, expected=expected)
        except InternalTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid internal service token",
            ) from exc
        return
    if not expected:
        # 本地降级实现且未配置令牌：视为开发环境放行（生产部署必须配置）。
        return
    if x_internal_token is None or not secrets.compare_digest(
        x_internal_token, expected
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid internal service token",
        )
