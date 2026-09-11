"""计费路由：套餐 / 订阅 / 下单（转发 billing 服务）。

安全边界：BFF 验完 JWT 后以 X-User-Id + X-Internal-Token 注入用户身份，
billing 服务不直接面对公网（webhook 除外，走 nginx 单独入口）。
响应体与状态码原样透传（错误格式同为 {"error": {...}} 约定）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import Response

from eduagent_shared.config import get_settings
from eduagent_shared.logging import get_logger

from ..deps import get_current_user, get_http_client
from ..errors import AppError

router = APIRouter(prefix="/billing", tags=["billing"])
log = get_logger("bff.billing")


async def _forward(
    method: str,
    path: str,
    user_id: uuid.UUID,
    client: httpx.AsyncClient,
    json_body: dict | None = None,
) -> Response:
    settings = get_settings()
    headers = {
        "X-Internal-Token": settings.internal_service_token,
        "X-User-Id": str(user_id),  # 服务端注入，客户端不可伪造
    }
    try:
        resp = await client.request(
            method,
            f"{settings.billing_url}{path}",
            headers=headers,
            json=json_body,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        log.warning("billing_forward_error", path=path, error=str(exc))
        raise AppError(502, "billing_unavailable", "计费服务暂不可用") from exc
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


@router.get("/plans")
async def list_plans(
    user=Depends(get_current_user),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> Response:
    """在售套餐列表。"""
    return await _forward("GET", "/billing/plans", user.id, client)


@router.get("/subscription")
async def get_subscription(
    user=Depends(get_current_user),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> Response:
    """当前订阅 + 本期额度用量。"""
    return await _forward("GET", "/billing/subscription", user.id, client)


@router.post("/orders")
async def create_order(
    payload: dict,
    user=Depends(get_current_user),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> Response:
    """下单（入参校验在 billing 服务完成，BFF 原样转发）。"""
    return await _forward("POST", "/billing/orders", user.id, client, json_body=payload)


@router.post("/orders/{order_id}/mock-confirm")
async def mock_confirm_order(
    order_id: str,
    user=Depends(get_current_user),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> Response:
    """mock 支付确认（仅 dev/test）：服务端持密钥签名并回调 billing webhook。

    客户端模拟支付成功走这里，webhook 密钥不出服务端；
    生产环境（app_env 非 dev/test）此端点 404 不存在。
    """
    settings = get_settings()
    if settings.app_env not in ("dev", "test"):
        raise AppError(404, "not_found", "接口不存在")
    secret = settings.billing_webhook_secrets.get("mock", "")
    if not secret:
        raise AppError(503, "mock_not_configured", "mock 渠道未配置验签密钥")
    body = json.dumps({
        "event": "payment_succeeded",
        "txn_id": f"mock-txn-{order_id}",
        "order_id": order_id,
        "channel_sub_id": f"mock-sub-{order_id}",
    }).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    try:
        resp = await client.post(
            f"{settings.billing_url}/billing/webhooks/mock",
            content=body,
            headers={"X-Billing-Signature": signature},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        log.warning("billing_mock_confirm_error", error=str(exc))
        raise AppError(502, "billing_unavailable", "计费服务暂不可用") from exc
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )
