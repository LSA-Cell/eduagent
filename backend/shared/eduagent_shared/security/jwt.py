"""JWT 签发与校验（PyJWT）。

access token 默认 15 分钟、refresh token 默认 7 天（由配置项控制）。
claims：sub=用户 ID，type=access/refresh，iat/exp。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from ..config import get_settings


class TokenError(ValueError):
    """Token 无效 / 过期 / 类型不符。"""


def _create_token(
    subject: str | uuid.UUID,
    token_type: str,
    expires: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    claims: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(
    user_id: str | uuid.UUID, extra_claims: dict[str, Any] | None = None
) -> str:
    """签发访问令牌（默认 15 分钟）。"""
    return _create_token(
        user_id,
        "access",
        timedelta(minutes=get_settings().access_token_expire_minutes),
        extra_claims,
    )


def create_refresh_token(user_id: str | uuid.UUID) -> str:
    """签发刷新令牌（默认 7 天）。"""
    return _create_token(
        user_id, "refresh", timedelta(days=get_settings().refresh_token_expire_days)
    )


def decode_token(token: str, *, expected_type: str | None = None) -> dict[str, Any]:
    """校验并解析 token；失败抛 TokenError。"""
    settings = get_settings()
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if expected_type is not None and claims.get("type") != expected_type:
        raise TokenError(f"token 类型不符：期望 {expected_type}")
    return claims
