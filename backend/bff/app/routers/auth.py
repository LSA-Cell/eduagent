"""鉴权路由：注册 / 登录 / 刷新令牌。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from eduagent_shared.config import get_settings
from eduagent_shared.security.jwt import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
)

from ..deps import get_user_store
from ..errors import AppError
from ..schemas import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserOut,
)
from ..services.password import hash_password, verify_password
from ..services.store import UserStore

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_tokens(user) -> TokenResponse:
    settings = get_settings()
    return TokenResponse(
        access_token=create_access_token(user.id, {"phone": user.phone}),
        refresh_token=create_refresh_token(user.id),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(
    payload: RegisterRequest,
    user_store: UserStore = Depends(get_user_store),
) -> RegisterResponse:
    """手机号 + 密码注册；成功即签发令牌（移动端免二次登录）。"""
    if await user_store.get_by_phone(payload.phone) is not None:
        raise AppError(409, "phone_taken", "该手机号已注册")
    user = await user_store.create(
        phone=payload.phone,
        password_hash=hash_password(payload.password),
        email=payload.email,
        grade_level=payload.grade_level,
        default_subject=payload.default_subject,
    )
    return RegisterResponse(user=UserOut.model_validate(user), tokens=_issue_tokens(user))


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    user_store: UserStore = Depends(get_user_store),
) -> TokenResponse:
    """手机号 + 密码登录，返回 JWT access + refresh。"""
    user = await user_store.get_by_phone(payload.phone)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise AppError(401, "invalid_credentials", "手机号或密码错误")
    return _issue_tokens(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    user_store: UserStore = Depends(get_user_store),
) -> TokenResponse:
    """刷新令牌换发新令牌对（refresh token 旋转）。"""
    try:
        claims = decode_token(payload.refresh_token, expected_type="refresh")
        user_id = uuid.UUID(str(claims["sub"]))
    except (TokenError, KeyError, ValueError):
        raise AppError(401, "invalid_refresh_token", "刷新令牌无效或已过期") from None
    user = await user_store.get_by_id(user_id)
    if user is None:
        raise AppError(401, "unauthorized", "用户不存在或已被删除")
    return _issue_tokens(user)
