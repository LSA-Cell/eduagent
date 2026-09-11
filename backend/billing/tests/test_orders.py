"""POST /billing/orders：下单校验（套餐/渠道/免费档拦截）。"""

from __future__ import annotations

from tests.conftest import user_headers


async def test_create_order_mock(client):
    resp = await client.post(
        "/billing/orders",
        json={"plan_id": "pro", "channel": "mock"},
        headers=user_headers(),
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["amount_cents"] == 6800
    assert body["currency"] == "CNY"
    assert body["payment_params"]["provider"] == "mock"
    assert body["payment_params"]["mock_token"].startswith("mockpay:")


async def test_free_plan_not_purchasable(client):
    resp = await client.post(
        "/billing/orders",
        json={"plan_id": "free", "channel": "mock"},
        headers=user_headers(),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "free_plan_not_purchasable"


async def test_unknown_plan_404(client):
    resp = await client.post(
        "/billing/orders",
        json={"plan_id": "nonexistent", "channel": "mock"},
        headers=user_headers(),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "plan_not_found"


async def test_unsupported_channel_400(client):
    """已定义但未接入的渠道（apple_iap 等）→ 400 channel_not_supported。"""
    resp = await client.post(
        "/billing/orders",
        json={"plan_id": "pro", "channel": "apple_iap"},
        headers=user_headers(),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "channel_not_supported"


async def test_order_does_not_activate_without_webhook(client, store):
    """安全红线：只下单不走 webhook，订阅不得激活（防客户端伪造支付成功）。"""
    resp = await client.post(
        "/billing/orders",
        json={"plan_id": "pro", "channel": "mock"},
        headers=user_headers(),
    )
    assert resp.status_code == 201

    status = await client.get("/billing/subscription", headers=user_headers())
    assert status.json()["status"] == "free"
