"""webhook 路由：POST /billing/webhooks/{channel}（渠道回调，公网可达）。

安全边界（spec §2.6/2.8）：
- 无 internal token，靠渠道签名验真：HMAC-SHA256(secret, raw_body)；
- secret 来自 BILLING_WEBHOOK_SECRETS，未配置 = 503 fail-closed；
- 验签失败 = 401，且不回显任何报文内容；
- 幂等：orders.channel_txn_id 唯一 + mark_order_paid 状态机守卫；
  重复回调返回 200 {"idempotent": true}，不重复激活。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Request

from eduagent_shared.config import get_settings
from eduagent_shared.logging import get_logger

from ..deps import get_billing_store
from ..errors import BillingError
from ..services import ops
from ..services.payments import SIGNATURE_HEADER, WebhookParseError, get_provider
from ..services.store import BillingStore

router = APIRouter(prefix="/billing/webhooks", tags=["billing-webhooks"])
log = get_logger("billing.webhooks")

_SUBSCRIPTION_DAYS = 30  # 月付周期（v1 仅月付，spec §1.2）


@router.post("/{channel}")
async def channel_webhook(
    channel: str,
    request: Request,
    x_billing_signature: str | None = Header(default=None, alias=SIGNATURE_HEADER),
    store: BillingStore = Depends(get_billing_store),
) -> dict:
    settings = get_settings()
    secret = settings.billing_webhook_secrets.get(channel, "")
    if not secret:
        # 未配置密钥的渠道：fail-closed，绝不“先放行再说”
        raise BillingError(
            503, f"渠道 {channel} 未配置验签密钥", "server_error", "webhook_not_configured"
        )
    provider = get_provider(channel)
    if provider is None:
        raise BillingError(
            400, f"渠道 {channel} 暂未接入", "invalid_request_error", "channel_not_supported"
        )

    raw_body = await request.body()
    if not provider.verify_signature(raw_body, x_billing_signature, secret):
        log.warning("webhook_signature_rejected", channel=channel)
        raise BillingError(401, "签名验证失败", "authentication_error", "invalid_signature")
    try:
        event = provider.parse_webhook(raw_body)
    except WebhookParseError as exc:
        raise BillingError(
            400, f"回调报文不合法: {exc}", "invalid_request_error", "invalid_webhook_payload"
        ) from exc

    # 全局幂等：同一 txn_id 已落过 paid 订单 → 直接确认，不重复激活
    existing = await store.get_order_by_txn(event.txn_id)
    if existing is not None and existing.status == "paid":
        return {"received": True, "idempotent": True}

    if event.event_type == "payment_succeeded":
        return await _handle_payment_succeeded(store, event)
    return await _handle_subscription_canceled(store, event)


async def _handle_payment_succeeded(store: BillingStore, event) -> dict:
    if event.order_id is None:
        raise BillingError(
            400, "缺少 order_id", "invalid_request_error", "invalid_webhook_payload"
        )
    order = await store.get_order(event.order_id)
    if order is None:
        raise BillingError(404, "订单不存在", "invalid_request_error", "order_not_found")
    paid = await store.mark_order_paid(
        order.id, event.txn_id, datetime.now(timezone.utc)
    )
    if paid is None:
        # 订单已被其他回调支付（txn 不同）：幂等确认但不重复激活
        if order.status == "paid":
            return {"received": True, "idempotent": True}
        raise BillingError(
            409, f"订单状态 {order.status} 不可支付", "invalid_request_error", "order_not_payable"
        )
    plan = await store.get_plan(order.plan_id)
    if plan is None:
        raise BillingError(500, "套餐数据缺失", "server_error", "plan_missing")
    await ops.activate_subscription(
        store,
        user_id=order.user_id,
        plan=plan,
        channel=event.channel,
        channel_sub_id=event.channel_sub_id,
        days=_SUBSCRIPTION_DAYS,
    )
    return {"received": True, "idempotent": False}


async def _handle_subscription_canceled(store: BillingStore, event) -> dict:
    """取消续订：订阅置 canceled（当期仍可用），到期由惰性过期收尾。"""
    if not event.channel_sub_id:
        raise BillingError(
            400, "缺少 channel_sub_id", "invalid_request_error", "invalid_webhook_payload"
        )
    # 按 channel_sub_id 定位：InMemory 与 SQL 实现共用 get_active_subscription 不便，
    # 这里经由订单反查用户（webhook 报文不含 user_id 是渠道常态）。
    order = None
    if event.order_id is not None:
        order = await store.get_order(uuid.UUID(str(event.order_id)))
    if order is None:
        # 降级：无法定位用户时只确认接收，由对账任务（v1.1）兜底
        log.warning(
            "webhook_cancel_unmatched", channel=event.channel, sub_id=event.channel_sub_id
        )
        return {"received": True, "unmatched": True}
    sub = await ops.expire_if_needed(store, await store.get_active_subscription(order.user_id))
    if sub is not None and sub.channel_sub_id == event.channel_sub_id:
        await store.set_subscription_status(sub.id, "canceled")
    return {"received": True}
