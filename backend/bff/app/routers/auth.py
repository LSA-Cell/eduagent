"""鉴权路由：注册 / 登录（密码·短信·邮箱·第三方·游客）/ 刷新 / 登出 / 设备管理 / 密码维护。

安全机制：
- refresh token 旋转（旧 jti 一次性作废）+ 设备会话 sid 真吊销（Redis）；
- 登录失败计数 → 阈值后强制图形验证码 → 超限锁定账号（Redis，fail-open 告警）；
- 登出/踢下线：吊销设备会话 + access jti 黑名单（TTL 至自然过期）；
- 短信验证码：哈希存储、重发间隔、日上限、试错上限（见 services/verification.py）。
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header
from redis.exceptions import RedisError

from eduagent_shared.config import get_settings
from eduagent_shared.logging import get_logger
from eduagent_shared.security.jwt import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
)

from ..deps import get_current_user, get_http_client, get_redis, get_user_store
from ..errors import AppError
from ..schemas import (
    CaptchaResponse,
    DeviceSessionOut,
    EmailLoginRequest,
    GuestLoginRequest,
    GuestUpgradeRequest,
    LoginRequest,
    OAuthLoginRequest,
    PasswordChangeRequest,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    SmsCodeRequest,
    SmsCodeResponse,
    SmsLoginRequest,
    TokenResponse,
    UserOut,
)
from ..services import devices, verification
from ..services.oauth import resolve_apple_sub, resolve_wechat_openid
from ..services.password import hash_password, verify_password
from ..services.store import UserStore

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger("bff.auth")

_USED_REFRESH_PREFIX = "rfused"
_LOGIN_FAIL_PREFIX = "loginfail"


async def _auth_header(
    authorization: Annotated[str | None, Header()] = None,
) -> str | None:
    """透传 Authorization 头（登出/设备列表需要 jti/sid，自行解析 claims）。"""
    return authorization


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _issue_tokens(user, *, sid: str | None = None) -> TokenResponse:
    settings = get_settings()
    claims = {"phone": user.phone, "guest": bool(getattr(user, "is_guest", False))}
    if sid:
        claims["sid"] = sid
    return TokenResponse(
        access_token=create_access_token(user.id, claims),
        refresh_token=create_refresh_token(user.id, {"sid": sid} if sid else None),
        expires_in=settings.access_token_expire_minutes * 60,
    )


async def _session_tokens(
    redis: aioredis.Redis,
    user,
    *,
    device_id: str | None,
    device_name: str | None,
) -> TokenResponse:
    """创建设备会话并签发令牌对；单设备登录模式下吊销同账号其他会话。"""
    sid = await devices.create_session(
        redis, user_id=user.id, device_id=device_id, device_name=device_name
    )
    if get_settings().single_device_login:
        await devices.revoke_all_sessions(redis, user.id, except_sid=sid)
    return _issue_tokens(user, sid=sid)


def _fail_key(account: str) -> str:
    return f"{_LOGIN_FAIL_PREFIX}:{account}"


async def _check_login_guard(
    redis: aioredis.Redis,
    *,
    account: str,
    captcha_id: str | None,
    captcha_code: str | None,
) -> None:
    """登录前置闸口：账号锁定检查 → 验证码检查（达阈值后强制）。Redis 故障 fail-open。"""
    settings = get_settings()
    try:
        failures = int(await redis.get(_fail_key(account)) or 0)
    except RedisError as exc:
        log.warning("login_guard_redis_error", error=str(exc))
        return
    if failures >= settings.login_max_failures:
        raise AppError(
            423,
            "account_locked",
            f"失败次数过多，账号已锁定，请 {settings.login_lock_minutes} 分钟后再试",
            {"retry_after_minutes": settings.login_lock_minutes},
        )
    if failures >= settings.login_captcha_threshold:
        if captcha_id and captcha_code:
            if await verification.verify_captcha(
                redis, captcha_id=captcha_id, code=captcha_code
            ):
                return
            # 验证码错误同样计为一次失败尝试，否则锁定永远无法生效
            await _record_login_failure(redis, account)
        raise AppError(
            401,
            "captcha_required",
            "请先完成图形验证码校验",
            {"captcha_required": True},
        )


async def _record_login_failure(redis: aioredis.Redis, account: str) -> None:
    """失败计数 +1，TTL = 锁定时长（窗口内累计）。"""
    settings = get_settings()
    try:
        n = await redis.incr(_fail_key(account))
        if n == 1:
            await redis.expire(_fail_key(account), settings.login_lock_minutes * 60)
    except RedisError as exc:
        log.warning("login_fail_mark_redis_error", error=str(exc))


async def _clear_login_failures(redis: aioredis.Redis, account: str) -> None:
    try:
        await redis.delete(_fail_key(account))
    except RedisError as exc:
        log.warning("login_fail_clear_redis_error", error=str(exc))


def _bearer_claims(authorization: str | None) -> dict:
    """从 Authorization 头解析 access token claims（登出等需要 jti/sid 的场景）。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError(401, "unauthorized", "缺少访问令牌")
    try:
        return decode_token(authorization[7:].strip(), expected_type="access")
    except TokenError:
        raise AppError(401, "unauthorized", "访问令牌无效或已过期") from None


async def _revoke_current(
    redis: aioredis.Redis, claims: dict, *, all_sessions: bool = False
) -> None:
    """登出：access jti 黑名单（至自然过期）+ 吊销设备会话。"""
    jti = claims.get("jti")
    exp = claims.get("exp")
    if jti and exp:
        await devices.blacklist_access_jti(
            redis, str(jti), max(0, int(exp) - int(time.time()))
        )
    user_id = claims.get("sub")
    if all_sessions and user_id:
        await devices.revoke_all_sessions(redis, uuid.UUID(str(user_id)))
    else:
        sid = claims.get("sid")
        if sid:
            await devices.revoke_session(redis, str(sid))


# ---------------------------------------------------------------------------
# 注册 / 登录
# ---------------------------------------------------------------------------


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(
    payload: RegisterRequest,
    user_store: UserStore = Depends(get_user_store),
    redis: aioredis.Redis = Depends(get_redis),
) -> RegisterResponse:
    """手机号 + 密码注册；成功即签发令牌（移动端免二次登录）。

    SMS_VERIFY_REGISTER=true（默认）时必须携带短信验证码（scene=register）。
    """
    if get_settings().sms_verify_register:
        if not payload.sms_code:
            raise AppError(
                422, "sms_code_required", "请先获取并填写短信验证码", {"sms_required": True}
            )
        if not await verification.verify_sms_code(
            redis, phone=payload.phone, scene="register", code=payload.sms_code
        ):
            raise AppError(422, "invalid_sms_code", "短信验证码错误或已过期")
    if await user_store.get_by_phone(payload.phone) is not None:
        raise AppError(409, "phone_taken", "该手机号已注册")
    user = await user_store.create(
        phone=payload.phone,
        password_hash=hash_password(payload.password),
        email=payload.email,
        grade_level=payload.grade_level,
        default_subject=payload.default_subject,
    )
    tokens = await _session_tokens(
        redis, user, device_id=payload.device_id, device_name=payload.device_name
    )
    return RegisterResponse(user=UserOut.model_validate(user), tokens=tokens)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    user_store: UserStore = Depends(get_user_store),
    redis: aioredis.Redis = Depends(get_redis),
) -> TokenResponse:
    """手机号 + 密码登录；连续失败触发图形验证码 / 账号锁定。"""
    await _check_login_guard(
        redis,
        account=payload.phone,
        captcha_id=payload.captcha_id,
        captcha_code=payload.captcha_code,
    )
    user = await user_store.get_by_phone(payload.phone)
    if (
        user is None
        or user.password_hash is None
        or not verify_password(payload.password, user.password_hash)
    ):
        await _record_login_failure(redis, payload.phone)
        raise AppError(401, "invalid_credentials", "手机号或密码错误")
    await _clear_login_failures(redis, payload.phone)
    return await _session_tokens(
        redis, user, device_id=payload.device_id, device_name=payload.device_name
    )


@router.post("/login-email", response_model=TokenResponse)
async def login_email(
    payload: EmailLoginRequest,
    user_store: UserStore = Depends(get_user_store),
    redis: aioredis.Redis = Depends(get_redis),
) -> TokenResponse:
    """邮箱 + 密码登录（适用于注册时绑定了邮箱的账号）。"""
    account = payload.email.lower()
    await _check_login_guard(redis, account=account, captcha_id=None, captcha_code=None)
    user = await user_store.get_by_email(payload.email)
    if (
        user is None
        or user.password_hash is None
        or not verify_password(payload.password, user.password_hash)
    ):
        await _record_login_failure(redis, account)
        raise AppError(401, "invalid_credentials", "邮箱或密码错误")
    await _clear_login_failures(redis, account)
    return await _session_tokens(
        redis, user, device_id=payload.device_id, device_name=payload.device_name
    )


@router.post("/login-sms", response_model=TokenResponse)
async def login_sms(
    payload: SmsLoginRequest,
    user_store: UserStore = Depends(get_user_store),
    redis: aioredis.Redis = Depends(get_redis),
) -> TokenResponse:
    """短信验证码登录；手机号未注册时自动建号（无密码，可后续设置）。"""
    if not await verification.verify_sms_code(
        redis, phone=payload.phone, scene="login", code=payload.code
    ):
        raise AppError(422, "invalid_sms_code", "短信验证码错误或已过期")
    user = await user_store.get_by_phone(payload.phone)
    if user is None:
        user = await user_store.create(phone=payload.phone)
    return await _session_tokens(
        redis, user, device_id=payload.device_id, device_name=payload.device_name
    )


@router.post("/guest", response_model=TokenResponse)
async def guest_login(
    payload: GuestLoginRequest,
    user_store: UserStore = Depends(get_user_store),
    redis: aioredis.Redis = Depends(get_redis),
) -> TokenResponse:
    """游客登录：设备 ID 换令牌，同一设备重进找回同一游客账号（含历史数据）。"""
    if not get_settings().guest_login_enabled:
        raise AppError(403, "guest_disabled", "游客模式未开启")
    user = await user_store.get_by_device_id(payload.device_id)
    if user is None:
        user = await user_store.create(is_guest=True, device_id=payload.device_id)
        log.info("guest_created", user_id=str(user.id))
    return await _session_tokens(
        redis, user, device_id=payload.device_id, device_name=payload.device_name
    )


@router.post("/upgrade", response_model=RegisterResponse)
async def upgrade_guest(
    payload: GuestUpgradeRequest,
    user=Depends(get_current_user),
    user_store: UserStore = Depends(get_user_store),
    redis: aioredis.Redis = Depends(get_redis),
) -> RegisterResponse:
    """游客转正：绑定手机号 + 密码（短信验证码核验），原游客数据全部保留。"""
    if not getattr(user, "is_guest", False):
        raise AppError(409, "not_guest", "当前账号已是正式账号")
    if not await verification.verify_sms_code(
        redis, phone=payload.phone, scene="register", code=payload.sms_code
    ):
        raise AppError(422, "invalid_sms_code", "短信验证码错误或已过期")
    if await user_store.get_by_phone(payload.phone) is not None:
        raise AppError(409, "phone_taken", "该手机号已注册")
    updated = await user_store.upgrade_guest(
        user.id, phone=payload.phone, password_hash=hash_password(payload.password)
    )
    if updated is None:
        raise AppError(404, "user_not_found", "用户不存在")
    return RegisterResponse(
        user=UserOut.model_validate(updated), tokens=_issue_tokens(updated)
    )


@router.post("/oauth/{provider}", response_model=TokenResponse)
async def oauth_login(
    provider: str,
    payload: OAuthLoginRequest,
    user_store: UserStore = Depends(get_user_store),
    redis: aioredis.Redis = Depends(get_redis),
    http=Depends(get_http_client),
) -> TokenResponse:
    """第三方登录（wechat / apple）；首次登录自动建号。

    未配置对应凭据时 503 fail-closed；dev/test 环境可用 dev_openid 直通联调。
    """
    if provider == "wechat":
        openid = await resolve_wechat_openid(
            http, code=payload.code, dev_openid=payload.dev_openid
        )
        user = await user_store.get_by_wechat_openid(openid)
        if user is None:
            user = await user_store.create(wechat_openid=openid)
    elif provider == "apple":
        sub = await resolve_apple_sub(
            identity_token=payload.identity_token, dev_openid=payload.dev_openid
        )
        user = await user_store.get_by_apple_sub(sub)
        if user is None:
            user = await user_store.create(apple_sub=sub)
    else:
        raise AppError(422, "unsupported_provider", "暂不支持的第三方登录方式")
    return await _session_tokens(
        redis, user, device_id=payload.device_id, device_name=payload.device_name
    )


# ---------------------------------------------------------------------------
# 验证码
# ---------------------------------------------------------------------------


@router.post("/sms-code", response_model=SmsCodeResponse)
async def send_sms_code(
    payload: SmsCodeRequest,
    redis: aioredis.Redis = Depends(get_redis),
    http=Depends(get_http_client),
) -> SmsCodeResponse:
    """下发短信验证码（scene=register/login/reset）。

    reset 场景要求手机号已注册；dev/test 环境响应附带 dev_code 便于联调。
    """
    return SmsCodeResponse.model_validate(
        await verification.request_sms_code(
            redis, http, phone=payload.phone, scene=payload.scene
        )
    )


@router.get("/captcha", response_model=CaptchaResponse)
async def get_captcha(redis: aioredis.Redis = Depends(get_redis)) -> CaptchaResponse:
    """生成图形验证码（SVG）；连续登录失败达阈值后登录接口要求携带。"""
    return CaptchaResponse.model_validate(await verification.create_captcha(redis))


# ---------------------------------------------------------------------------
# 刷新 / 登出 / 设备管理
# ---------------------------------------------------------------------------


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    user_store: UserStore = Depends(get_user_store),
    redis: aioredis.Redis = Depends(get_redis),
) -> TokenResponse:
    """刷新令牌换发新令牌对（refresh token 旋转：旧 jti 一次性作废）。

    携带 sid 的新令牌同时校验设备会话：会话被吊销（登出/踢下线/单设备互踢）
    即 refresh 真失效。Redis 故障时 fail-open 并告警，与限流器同一可用性策略。
    """
    try:
        claims = decode_token(payload.refresh_token, expected_type="refresh")
        user_id = uuid.UUID(str(claims["sub"]))
    except (TokenError, KeyError, ValueError):
        raise AppError(401, "invalid_refresh_token", "刷新令牌无效或已过期") from None

    sid = claims.get("sid")
    if sid and not await devices.is_session_active(redis, str(sid)):
        raise AppError(401, "invalid_refresh_token", "登录状态已失效，请重新登录")

    jti = claims.get("jti")
    settings = get_settings()
    ttl_seconds = settings.refresh_token_expire_days * 86400
    if jti:
        try:
            if await redis.exists(f"{_USED_REFRESH_PREFIX}:{jti}"):
                raise AppError(
                    401, "invalid_refresh_token", "刷新令牌已失效，请重新登录"
                )
        except RedisError as exc:
            log.warning("refresh_jti_check_redis_error", error=str(exc))

    user = await user_store.get_by_id(user_id)
    if user is None:
        raise AppError(401, "unauthorized", "用户不存在或已被删除")

    tokens = _issue_tokens(user, sid=str(sid) if sid else None)
    if jti:
        try:
            await redis.setex(f"{_USED_REFRESH_PREFIX}:{jti}", ttl_seconds, "1")
        except RedisError as exc:
            log.warning("refresh_jti_mark_redis_error", error=str(exc))
    if sid:
        await devices.touch_session(redis, str(sid))
    return tokens


@router.post("/logout", status_code=204)
async def logout(
    authorization: str | None = Depends(_auth_header),
    redis: aioredis.Redis = Depends(get_redis),
) -> None:
    """登出：吊销当前设备会话 + access token 黑名单（即刻生效，不再只是清本地）。"""
    claims = _bearer_claims(authorization)
    await _revoke_current(redis, claims)


@router.post("/logout-all", status_code=204)
async def logout_all(
    authorization: str | None = Depends(_auth_header),
    redis: aioredis.Redis = Depends(get_redis),
) -> None:
    """全部设备下线：吊销本账号所有设备会话（其他设备 refresh 即刻失效）。"""
    claims = _bearer_claims(authorization)
    await _revoke_current(redis, claims, all_sessions=True)


@router.get("/devices", response_model=list[DeviceSessionOut])
async def list_devices(
    authorization: str | None = Depends(_auth_header),
    redis: aioredis.Redis = Depends(get_redis),
) -> list[DeviceSessionOut]:
    """设备管理：列出当前账号的活跃登录设备。"""
    claims = _bearer_claims(authorization)
    sessions = await devices.list_sessions(redis, uuid.UUID(str(claims["sub"])))
    current_sid = claims.get("sid")
    return [
        DeviceSessionOut(
            sid=s["sid"],
            device_id=s.get("device_id", ""),
            device_name=s.get("device_name", ""),
            created_at=int(s.get("created_at", "0")),
            last_seen=int(s.get("last_seen", "0")),
            current=s["sid"] == current_sid,
        )
        for s in sessions
    ]


@router.delete("/devices/{sid}", status_code=204)
async def revoke_device(
    sid: str,
    user=Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis),
) -> None:
    """踢人下线：吊销指定设备会话（仅限本账号的会话）。"""
    sessions = await devices.list_sessions(redis, user.id)
    if sid not in {s["sid"] for s in sessions}:
        raise AppError(404, "device_not_found", "设备会话不存在或已失效")
    await devices.revoke_session(redis, sid)


# ---------------------------------------------------------------------------
# 密码维护
# ---------------------------------------------------------------------------


@router.post("/password/change", status_code=204)
async def change_password(
    payload: PasswordChangeRequest,
    authorization: str | None = Depends(_auth_header),
    user_store: UserStore = Depends(get_user_store),
    redis: aioredis.Redis = Depends(get_redis),
) -> None:
    """修改密码：核验旧密码；成功后其他设备会话全部吊销（本机保留）。"""
    claims = _bearer_claims(authorization)
    user = await user_store.get_by_id(uuid.UUID(str(claims["sub"])))
    if user is None:
        raise AppError(401, "unauthorized", "用户不存在或已被删除")
    if user.password_hash is None:
        raise AppError(409, "no_password", "当前账号尚未设置密码，请使用找回密码流程")
    if not verify_password(payload.old_password, user.password_hash):
        raise AppError(401, "invalid_credentials", "原密码错误")
    await user_store.set_password(user.id, hash_password(payload.new_password))
    sid = claims.get("sid")
    await devices.revoke_all_sessions(
        redis, user.id, except_sid=str(sid) if sid else None
    )


@router.post("/password/reset", status_code=204)
async def reset_password(
    payload: PasswordResetRequest,
    user_store: UserStore = Depends(get_user_store),
    redis: aioredis.Redis = Depends(get_redis),
) -> None:
    """找回密码：短信验证码（scene=reset）核验通过后重置，并吊销全部设备会话。"""
    if not await verification.verify_sms_code(
        redis, phone=payload.phone, scene="reset", code=payload.sms_code
    ):
        raise AppError(422, "invalid_sms_code", "短信验证码错误或已过期")
    user = await user_store.get_by_phone(payload.phone)
    if user is None:
        raise AppError(404, "user_not_found", "该手机号尚未注册")
    await user_store.set_password(user.id, hash_password(payload.new_password))
    await devices.revoke_all_sessions(redis, user.id)
