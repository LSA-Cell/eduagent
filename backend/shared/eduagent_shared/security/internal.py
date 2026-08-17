"""服务间 INTERNAL_SERVICE_TOKEN 校验。

shared 不依赖 FastAPI，这里提供纯函数；各服务自行包装成 FastAPI 依赖
（读取 X-Internal-Token 头后调用 verify_internal_token）。
"""

from __future__ import annotations

import secrets

from ..config import get_settings


class InternalTokenError(PermissionError):
    """服务间令牌缺失或不匹配。"""


def verify_internal_token(token: str | None, *, expected: str | None = None) -> None:
    """校验服务间令牌，失败抛 InternalTokenError。"""
    expected = expected if expected is not None else get_settings().internal_service_token
    if not token or not expected or not secrets.compare_digest(token, expected):
        raise InternalTokenError("服务间令牌校验失败")
