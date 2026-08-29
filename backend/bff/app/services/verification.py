"""验证码服务：短信验证码 + 图形验证码。

短信验证码：
- Redis 仅存 SHA-256 哈希（不落明文），带 TTL、重发间隔、日发送上限、试错次数上限；
- provider=log：验证码只写日志，仅 dev/test 可用（生产启动即被 Settings 校验拒绝）；
  dev/test 下响应附带 dev_code 便于联调与自动化测试；
- provider=webhook：POST JSON 到 SMS_WEBHOOK_URL（携带内部服务令牌），由外部短信网关落地。

图形验证码：
- 4 位无歧义字符，SVG 渲染（无 Pillow 依赖），答案哈希存 Redis 5 分钟、一次性消费。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid

import httpx
import redis.asyncio as aioredis
from redis.exceptions import RedisError

from eduagent_shared.config import Settings, get_settings
from eduagent_shared.logging import get_logger

from ..errors import AppError

log = get_logger("bff.verification")

_SMS_CODE_PREFIX = "sms"
_SMS_INTERVAL_PREFIX = "sms_int"
_SMS_DAILY_PREFIX = "sms_day"
_CAPTCHA_PREFIX = "captcha"

# 去掉易混淆字符（0/O、1/I/L）
_CAPTCHA_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

SmsScene = str  # register / login / reset
_SMS_SCENES = ("register", "login", "reset")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 短信验证码
# ---------------------------------------------------------------------------


async def request_sms_code(
    redis: aioredis.Redis,
    http: httpx.AsyncClient,
    *,
    phone: str,
    scene: SmsScene,
) -> dict[str, object]:
    """生成并下发短信验证码；返回 {expires_in, dev_code?}。"""
    settings = get_settings()
    if scene not in _SMS_SCENES:
        raise AppError(422, "invalid_scene", "不支持的验证码场景")

    interval_key = f"{_SMS_INTERVAL_PREFIX}:{scene}:{phone}"
    daily_key = f"{_SMS_DAILY_PREFIX}:{phone}"
    try:
        if await redis.exists(interval_key):
            raise AppError(
                429,
                "sms_too_frequent",
                f"发送过于频繁，请 {settings.sms_send_interval_seconds} 秒后再试",
            )
        daily = await redis.incr(daily_key)
        if daily == 1:
            await redis.expire(daily_key, 86400)
        if daily > settings.sms_daily_limit:
            raise AppError(429, "sms_daily_limit", "今日验证码发送次数已达上限")
    except RedisError as exc:
        log.warning("sms_ratelimit_redis_error", error=str(exc))

    code = f"{secrets.randbelow(10 ** settings.sms_code_length):0{settings.sms_code_length}d}"
    try:
        await redis.hset(
            f"{_SMS_CODE_PREFIX}:{scene}:{phone}",
            mapping={"code_hash": _hash(code), "attempts": "0"},
        )
        await redis.expire(
            f"{_SMS_CODE_PREFIX}:{scene}:{phone}", settings.sms_code_expire_minutes * 60
        )
        await redis.setex(interval_key, settings.sms_send_interval_seconds, "1")
    except RedisError as exc:
        log.error("sms_store_redis_error", error=str(exc))
        raise AppError(503, "sms_unavailable", "验证码服务暂不可用，请稍后再试") from None

    await _send_sms(http, settings, phone=phone, code=code, scene=scene)

    result: dict[str, object] = {"expires_in": settings.sms_code_expire_minutes * 60}
    if settings.app_env in ("dev", "test"):
        result["dev_code"] = code  # 仅开发/测试环境回显，生产绝不返回
    return result


async def verify_sms_code(
    redis: aioredis.Redis, *, phone: str, scene: SmsScene, code: str
) -> bool:
    """校验短信验证码；成功即一次性消费。Redis 故障 fail-closed（验证码宁可不可用）。"""
    settings = get_settings()
    key = f"{_SMS_CODE_PREFIX}:{scene}:{phone}"
    try:
        data = await redis.hgetall(key)
    except RedisError as exc:
        log.error("sms_verify_redis_error", error=str(exc))
        return False
    if not data:
        return False
    attempts = int(data.get("attempts", "0")) + 1
    if attempts > settings.sms_max_verify_attempts:
        await redis.delete(key)
        return False
    if not secrets.compare_digest(data.get("code_hash", ""), _hash(code)):
        await redis.hset(key, "attempts", str(attempts))
        return False
    await redis.delete(key)
    return True


async def _send_sms(
    http: httpx.AsyncClient,
    settings: Settings,
    *,
    phone: str,
    code: str,
    scene: SmsScene,
) -> None:
    """下发验证码。log provider 仅 dev/test；webhook 失败视为发送失败（不静默吞错）。"""
    if settings.sms_provider == "log":
        log.info("sms_code_sent", phone=phone, scene=scene, code=code)
        return
    if settings.sms_provider == "webhook":
        if not settings.sms_webhook_url:
            log.error("sms_webhook_url_missing")
            raise AppError(503, "sms_unavailable", "短信服务未配置，请稍后再试")
        try:
            resp = await http.post(
                settings.sms_webhook_url,
                json={"phone": phone, "code": code, "scene": scene},
                headers={"X-Internal-Token": settings.internal_service_token},
                timeout=10.0,
            )
            resp.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            log.error("sms_webhook_error", error=str(exc))
            raise AppError(503, "sms_unavailable", "短信发送失败，请稍后再试") from None
        return
    log.error("sms_provider_unknown", provider=settings.sms_provider)
    raise AppError(503, "sms_unavailable", "短信服务配置错误")


# ---------------------------------------------------------------------------
# 图形验证码
# ---------------------------------------------------------------------------


async def create_captcha(redis: aioredis.Redis) -> dict[str, object]:
    """生成图形验证码，返回 {captcha_id, svg, expires_in}。"""
    settings = get_settings()
    code = "".join(secrets.choice(_CAPTCHA_ALPHABET) for _ in range(4))
    captcha_id = uuid.uuid4().hex
    try:
        await redis.setex(
            f"{_CAPTCHA_PREFIX}:{captcha_id}",
            settings.captcha_expire_minutes * 60,
            _hash(code.lower()),
        )
    except RedisError as exc:
        log.error("captcha_store_redis_error", error=str(exc))
        raise AppError(503, "captcha_unavailable", "验证码服务暂不可用") from None
    return {
        "captcha_id": captcha_id,
        "svg": _render_svg(code),
        "expires_in": settings.captcha_expire_minutes * 60,
    }


async def verify_captcha(redis: aioredis.Redis, *, captcha_id: str, code: str) -> bool:
    """校验图形验证码（一次性消费）。Redis 故障 fail-closed。"""
    key = f"{_CAPTCHA_PREFIX}:{captcha_id}"
    try:
        expected = await redis.getdel(key)
    except RedisError as exc:
        log.error("captcha_verify_redis_error", error=str(exc))
        return False
    if expected is None:
        return False
    return secrets.compare_digest(expected, _hash(code.strip().lower()))


def _render_svg(code: str) -> str:
    """渲染带干扰线的 SVG 验证码（纯标准库，无图片依赖）。"""
    width, height = 132, 44
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" rx="6" fill="#f5f6f8"/>',
    ]
    for _ in range(4):  # 干扰线
        x1, y1 = secrets.randbelow(width), secrets.randbelow(height)
        x2, y2 = secrets.randbelow(width), secrets.randbelow(height)
        color = secrets.randbelow(0x777777) + 0x333333
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="#{color:06x}" stroke-width="1" opacity="0.5"/>'
        )
    for i, ch in enumerate(code):
        x = 14 + i * 30 + secrets.randbelow(6)
        y = 28 + secrets.randbelow(8) - 4
        rotate = secrets.randbelow(50) - 25
        color = 0x111111 + secrets.randbelow(0x555555)
        parts.append(
            f'<text x="{x}" y="{y}" font-family="monospace" font-size="26" '
            f'font-weight="700" fill="#{color:06x}" '
            f'transform="rotate({rotate} {x} {y})">{ch}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)
