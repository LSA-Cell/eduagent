"""支付渠道适配层。

真实渠道（Apple IAP / 微信 / 支付宝 / Stripe）在 v1.1 接入；
本期只实现 MockPaymentProvider（dev/test 全链路验证用），
但所有 webhook / 下单流程都走 PaymentProvider 协议，接真渠道时无需改流程代码。

安全约定：
- webhook 验签：HMAC-SHA256(secret, raw_body) hex，经 X-Billing-Signature 头传入；
  未配置 secret 的渠道一律 503 fail-closed（在路由层判定）。
- 验签使用 hmac.compare_digest 防时序侧信道。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

SIGNATURE_HEADER = "X-Billing-Signature"


@dataclass
class WebhookEvent:
    """归一化后的渠道回调事件。"""

    event_type: str  # payment_succeeded / subscription_canceled
    channel: str
    txn_id: str  # 渠道交易号（幂等键）
    order_id: uuid.UUID | None = None
    channel_sub_id: str | None = None


class WebhookParseError(Exception):
    """回调报文无法解析（验签通过后仍可能是脏数据）。"""


class PaymentProvider(Protocol):
    channel: str

    def verify_signature(self, raw_body: bytes, signature: str | None, secret: str) -> bool: ...
    def parse_webhook(self, raw_body: bytes) -> WebhookEvent: ...
    def create_payment_params(self, order_id: uuid.UUID, plan_id: str) -> dict[str, Any]: ...


class MockPaymentProvider:
    """Mock 渠道：dev/test 全链路（下单 → 回调 → 激活）离线验证。

    报文格式（JSON）：
      {"event": "payment_succeeded", "txn_id": "mock-txn-1",
       "order_id": "<uuid>", "channel_sub_id": "mock-sub-1"}
      {"event": "subscription_canceled", "txn_id": "mock-txn-2",
       "channel_sub_id": "mock-sub-1"}
    """

    channel = "mock"

    def verify_signature(
        self, raw_body: bytes, signature: str | None, secret: str
    ) -> bool:
        if not signature:
            return False
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_webhook(self, raw_body: bytes) -> WebhookEvent:
        try:
            payload = json.loads(raw_body)
        except ValueError as exc:
            raise WebhookParseError("回调报文不是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise WebhookParseError("回调报文必须是 JSON 对象")
        event_type = payload.get("event")
        txn_id = payload.get("txn_id")
        if event_type not in ("payment_succeeded", "subscription_canceled"):
            raise WebhookParseError(f"未知事件类型: {event_type!r}")
        if not txn_id or not isinstance(txn_id, str):
            raise WebhookParseError("缺少 txn_id")
        order_id = payload.get("order_id")
        try:
            parsed_order_id = uuid.UUID(str(order_id)) if order_id else None
        except ValueError as exc:
            raise WebhookParseError("order_id 不是合法 UUID") from exc
        return WebhookEvent(
            event_type=event_type,
            channel=self.channel,
            txn_id=txn_id,
            order_id=parsed_order_id,
            channel_sub_id=payload.get("channel_sub_id"),
        )

    def create_payment_params(self, order_id: uuid.UUID, plan_id: str) -> dict[str, Any]:
        """mock 渠道无需真实收银台：返回可直接构造回调的令牌，客户端 dev 环境一键确认。"""
        return {
            "provider": "mock",
            "mock_token": f"mockpay:{order_id}",
            "instructions": "dev/test 环境：使用 mock_token 构造 webhook 回调即完成支付",
        }


_PROVIDERS: dict[str, PaymentProvider] = {
    MockPaymentProvider.channel: MockPaymentProvider(),
}


def get_provider(channel: str) -> PaymentProvider | None:
    """按渠道取适配器；未接入渠道返回 None（路由层转 400/503）。"""
    return _PROVIDERS.get(channel)
