"""内部服务间鉴权依赖。

统一走 eduagent_shared.security.internal（纯函数，失败抛 InternalTokenError），
这里仅包装为 FastAPI 依赖。红线：共享包为唯一事实源，禁止本地等价实现；
未配置令牌时 shared 校验一律拒绝（fail-closed，不存在开发放行通道）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException, status

from eduagent_shared.security.internal import (
    InternalTokenError,
    verify_internal_token as _shared_verify,
)

from app.config import get_settings


async def verify_internal_token(
    x_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    """校验调用方内部服务令牌，失败返回 401。

    约定：网关注入 ``X-Internal-Token`` 头，值与 INTERNAL_SERVICE_TOKEN 一致。
    """
    try:
        _shared_verify(x_internal_token, expected=get_settings().internal_service_token)
    except InternalTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid internal service token",
        ) from exc
