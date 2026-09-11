"""POST /billing/webhooks/{channel}：验签 / 幂等 / 激活 / 取消。"""

from __future__ import annotations

import json
import uuid

from tests.conftest import (
    TEST_USER_ID,
    WEBHOOK_SECRET,
    create_paid_order,
    sign,
    user_headers,
    webhook_body,
)


async def _create_order(client, plan_id: str = "pro") -> dict:
    resp = await client.post(
        "/billing/orders",
        json={"plan_id": plan_id, "channel": "mock"},
        headers=user_headers(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_payment_succeeded_activates_subscription(client, store):
    order = await _create_order(client)
    body = webhook_body({
        "event": "payment_succeeded",
        "txn_id": "txn-001",
        "order_id": order["id"],
        "channel_sub_id": "sub-001",
    })

    resp = await client.post(
        "/billing/webhooks/mock",
        content=body,
        headers={"X-Billing-Signature": sign(body)},
    )

    assert resp.status_code == 200
    assert resp.json() == {"received": True, "idempotent": False}
    stored_order = await store.get_order_by_txn("txn-001")
    assert stored_order.status == "paid"
    assert stored_order.paid_at is not None
    sub = await store.get_active_subscription(TEST_USER_ID)
    assert sub is not None and sub.plan_id == "pro"
    assert sub.channel_sub_id == "sub-001"
    assert store.user_paid[TEST_USER_ID] is True
    # 额度当月对齐 pro 档
    from app.services.ops import current_period_start

    quota = await store.get_quota_period(TEST_USER_ID, current_period_start())
    assert quota.quota_cents == 3000


async def test_webhook_replay_is_idempotent(client, store):
    """同一 txn_id 重放：幂等确认，不重复激活、不重复改订单。"""
    order = await _create_order(client)
    body = webhook_body({
        "event": "payment_succeeded",
        "txn_id": "txn-002",
        "order_id": order["id"],
        "channel_sub_id": "sub-002",
    })
    headers = {"X-Billing-Signature": sign(body)}

    first = await client.post("/billing/webhooks/mock", content=body, headers=headers)
    second = await client.post("/billing/webhooks/mock", content=body, headers=headers)

    assert first.json()["idempotent"] is False
    assert second.json()["idempotent"] is True
    subs = [s for s in store.subs.values() if s.user_id == TEST_USER_ID and s.status == "active"]
    assert len(subs) == 1


async def test_bad_signature_rejected(client, store):
    order = await _create_order(client)
    body = webhook_body({
        "event": "payment_succeeded",
        "txn_id": "txn-003",
        "order_id": order["id"],
    })

    resp = await client.post(
        "/billing/webhooks/mock",
        content=body,
        headers={"X-Billing-Signature": "deadbeef"},
    )

    assert resp.status_code == 401
    assert (await store.get_order(uuid.UUID(order["id"]))).status == "pending"
    assert await store.get_active_subscription(TEST_USER_ID) is None


async def test_missing_signature_rejected(client):
    body = webhook_body({"event": "payment_succeeded", "txn_id": "txn-x"})
    resp = await client.post("/billing/webhooks/mock", content=body)
    assert resp.status_code == 401


async def test_unconfigured_channel_fail_closed(client):
    """未配置验签密钥的渠道：503 fail-closed（即使报文合法也不处理）。"""
    body = webhook_body({
        "event": "payment_succeeded",
        "txn_id": "txn-004",
        "order_id": "11111111-1111-1111-1111-111111111111",
    })
    resp = await client.post(
        "/billing/webhooks/stripe",
        content=body,
        headers={"X-Billing-Signature": sign(body)},
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "webhook_not_configured"


async def test_malformed_payload_400(client):
    body = b'{"event": "unknown_event", "txn_id": "t"}'
    resp = await client.post(
        "/billing/webhooks/mock",
        content=body,
        headers={"X-Billing-Signature": sign(body)},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_webhook_payload"


async def test_nonexistent_order_404(client):
    body = webhook_body({
        "event": "payment_succeeded",
        "txn_id": "txn-005",
        "order_id": "99999999-9999-9999-9999-999999999999",
    })
    resp = await client.post(
        "/billing/webhooks/mock",
        content=body,
        headers={"X-Billing-Signature": sign(body)},
    )
    assert resp.status_code == 404


async def test_subscription_canceled_marks_canceled(client, store):
    """取消续订：订阅置 canceled（当期可用），is_paid 保持至惰性过期。"""
    order = await create_paid_order(client, store)
    sub = await store.get_active_subscription(TEST_USER_ID)
    body = webhook_body({
        "event": "subscription_canceled",
        "txn_id": "txn-cancel-001",
        "order_id": order["id"],
        "channel_sub_id": sub.channel_sub_id,
    })

    resp = await client.post(
        "/billing/webhooks/mock",
        content=body,
        headers={"X-Billing-Signature": sign(body)},
    )

    assert resp.status_code == 200
    assert store.subs[sub.id].status == "canceled"
