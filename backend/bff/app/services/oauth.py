"""第三方登录校验：微信 / Apple。

- 微信：服务端用 code 调 ``api.weixin.qq.com/sns/oauth2/access_token`` 换 openid；
- Apple：校验 identity_token（ES256 JWT），公钥来自 ``appleid.apple.com/auth/keys``
  （PyJWT PyJWKClient），aud 必须等于 APPLE_CLIENT_ID；
- 未配置对应凭据时返回 503 ``oauth_not_configured``（fail-closed，绝不跳过校验）；
- dev/test 环境允许 dev_openid 直通，便于本地联调与自动化测试。
"""

from __future__ import annotations

import httpx
import jwt

from eduagent_shared.config import get_settings
from eduagent_shared.logging import get_logger

from ..errors import AppError

log = get_logger("bff.oauth")

_APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
_WECHAT_TOKEN_URL = "https://api.weixin.qq.com/sns/oauth2/access_token"

OAUTH_PROVIDERS = ("wechat", "apple")


async def resolve_wechat_openid(
    http: httpx.AsyncClient, *, code: str | None, dev_openid: str | None
) -> str:
    """微信 code → openid。dev/test 可用 dev_openid 直通。"""
    settings = get_settings()
    if settings.app_env in ("dev", "test") and dev_openid:
        return dev_openid
    if not code:
        raise AppError(422, "invalid_oauth_code", "缺少微信授权 code")
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        raise AppError(503, "oauth_not_configured", "微信登录未配置，请稍后再试")
    try:
        resp = await http.get(
            _WECHAT_TOKEN_URL,
            params={
                "appid": settings.wechat_app_id,
                "secret": settings.wechat_app_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
            timeout=10.0,
        )
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("wechat_code_exchange_error", error=str(exc))
        raise AppError(502, "oauth_upstream_error", "微信登录服务异常，请稍后再试") from None
    openid = data.get("openid")
    if not openid:
        log.warning("wechat_code_exchange_rejected", errcode=data.get("errcode"))
        raise AppError(401, "invalid_oauth_code", "微信授权已失效，请重新授权")
    return str(openid)


async def resolve_apple_sub(
    *, identity_token: str | None, dev_openid: str | None
) -> str:
    """校验 Apple identity_token（ES256），返回用户唯一标识 sub。"""
    settings = get_settings()
    if settings.app_env in ("dev", "test") and dev_openid:
        return dev_openid
    if not identity_token:
        raise AppError(422, "invalid_oauth_token", "缺少 Apple identity_token")
    if not settings.apple_client_id:
        raise AppError(503, "oauth_not_configured", "Apple 登录未配置，请稍后再试")
    try:
        jwk_client = jwt.PyJWKClient(_APPLE_JWKS_URL)
        signing_key = jwk_client.get_signing_key_from_jwt(identity_token)
        claims = jwt.decode(
            identity_token,
            signing_key.key,
            algorithms=["ES256"],
            audience=settings.apple_client_id,
        )
    except jwt.PyJWTError as exc:
        log.warning("apple_identity_token_invalid", error=str(exc))
        raise AppError(401, "invalid_oauth_token", "Apple 凭证无效或已过期") from None
    sub = claims.get("sub")
    if not sub:
        raise AppError(401, "invalid_oauth_token", "Apple 凭证缺少用户标识")
    return str(sub)
