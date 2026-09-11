"""BFF → billing 服务代理：身份注入 / 透传 / 上游故障 502。"""

from __future__ import annotations

import json

import httpx

from tests.conftest import auth_headers, register_and_login

PLANS_BODY = [
    {
        "id": "free",
        "name": "免费版",
        "price_cents": 0,
        "currency": "CNY",
        "monthly_quota_cents": 300,
        "allowed_models": ["deepseek-chat"],
        "features": {},
        "sort_order": 0,
    }
]


def _billing_upstream(captured: dict, body=PLANS_BODY, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["internal_token"] = request.headers.get("x-internal-token")
        captured["user_id"] = request.headers.get("x-user-id")
        captured["body"] = request.content.decode() if request.content else ""
        return httpx.Response(status, json=body)

    return handler


async def test_plans_proxied_with_identity(app, client):
    token = await register_and_login(client)
    captured: dict = {}
    app.state.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_billing_upstream(captured))
    )

    resp = await client.get("/api/v1/billing/plans", headers=auth_headers(token))

    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "free"
    assert captured["url"].endswith("/billing/plans")
    assert captured["internal_token"] == "test-internal-token"
    assert captured["user_id"]  # BFF 注入了用户身份


async def test_subscription_proxied(app, client):
    token = await register_and_login(client, phone="13800000002")
    body = {"plan": PLANS_BODY[0], "status": "free", "quota": {
        "period_start": "2026-09-01", "quota_cents": 300, "used_cents": 0,
        "remaining_cents": 300}}
    captured: dict = {}
    app.state.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_billing_upstream(captured, body=body))
    )

    resp = await client.get("/api/v1/billing/subscription", headers=auth_headers(token))

    assert resp.status_code == 200
    assert resp.json()["status"] == "free"
    assert captured["url"].endswith("/billing/subscription")


async def test_create_order_proxied(app, client):
    token = await register_and_login(client, phone="13800000003")
    order_body = {"id": "11111111-1111-1111-1111-111111111111", "plan_id": "pro",
                  "amount_cents": 6800, "currency": "CNY", "channel": "mock",
                  "status": "pending", "created_at": "2026-09-10T00:00:00Z",
                  "paid_at": None, "payment_params": {"provider": "mock"}}
    captured: dict = {}
    app.state.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_billing_upstream(captured, body=order_body, status=201))
    )

    resp = await client.post(
        "/api/v1/billing/orders",
        json={"plan_id": "pro", "channel": "mock"},
        headers=auth_headers(token),
    )

    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"
    assert json.loads(captured["body"])["plan_id"] == "pro"


async def test_billing_upstream_down_returns_502(app, client):
    token = await register_and_login(client, phone="13800000004")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    resp = await client.get("/api/v1/billing/plans", headers=auth_headers(token))

    assert resp.status_code == 502


async def test_billing_requires_auth(client):
    resp = await client.get("/api/v1/billing/plans")
    assert resp.status_code == 401


async def test_mock_confirm_signs_and_posts_webhook(app, client):
    """mock 支付确认：BFF 持密钥签名 → 回调 billing webhook（密钥不出服务端）。"""
    import hashlib
    import hmac
    import json as jsonlib

    token = await register_and_login(client, phone="13800000005")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["signature"] = request.headers.get("x-billing-signature")
        captured["body"] = request.content
        return httpx.Response(200, json={"received": True, "idempotent": False})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    order_id = "11111111-1111-1111-1111-111111111111"
    resp = await client.post(
        f"/api/v1/billing/orders/{order_id}/mock-confirm",
        headers=auth_headers(token),
    )

    assert resp.status_code == 200
    assert captured["url"].endswith("/billing/webhooks/mock")
    expected = hmac.new(
        b"test-webhook-secret", captured["body"], hashlib.sha256
    ).hexdigest()
    assert captured["signature"] == expected
    payload = jsonlib.loads(captured["body"])
    assert payload["event"] == "payment_succeeded"
    assert payload["order_id"] == order_id
