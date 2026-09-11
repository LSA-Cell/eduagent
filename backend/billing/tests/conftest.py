"""Billing 测试 fixtures：InMemory 仓储替代 PostgreSQL，全程离线。

种子套餐与 init.sql 一致（free / pro / teacher_pro）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import httpx
import pytest

from app import deps
from app.main import create_app
from app.services.store import InMemoryBillingStore

TEST_USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
WEBHOOK_SECRET = "test-webhook-secret"

INTERNAL_HEADERS = {"X-Internal-Token": "test-internal-token"}


def user_headers(user_id: uuid.UUID = TEST_USER_ID) -> dict:
    return {**INTERNAL_HEADERS, "X-User-Id": str(user_id)}


def sign(raw_body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def webhook_body(payload: dict) -> bytes:
    return json.dumps(payload).encode()


@pytest.fixture
def store() -> InMemoryBillingStore:
    s = InMemoryBillingStore()
    s.seed_plan(
        id="free", name="免费版", price_cents=0, monthly_quota_cents=300,
        allowed_models=["deepseek-chat", "gpt-4o-mini"], sort_order=0,
    )
    s.seed_plan(
        id="pro", name="Pro 会员", price_cents=6800, monthly_quota_cents=3000,
        allowed_models=[], sort_order=1,
    )
    s.seed_plan(
        id="teacher_pro", name="教师版", price_cents=9800, monthly_quota_cents=5000,
        allowed_models=[], features={"teacher_tools": True}, sort_order=2,
    )
    return s


@pytest.fixture
def app(store):
    application = create_app()
    application.dependency_overrides[deps.get_billing_store] = lambda: store
    return application


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def create_paid_order(client, store, user_id: uuid.UUID = TEST_USER_ID,
                            plan_id: str = "pro", channel: str = "mock") -> dict:
    """下单 → 模拟 webhook 支付成功，返回订单 JSON。"""
    resp = await client.post(
        "/billing/orders",
        json={"plan_id": plan_id, "channel": channel},
        headers=user_headers(user_id),
    )
    assert resp.status_code == 201, resp.text
    order = resp.json()
    body = webhook_body({
        "event": "payment_succeeded",
        "txn_id": f"txn-{order['id']}",
        "order_id": order["id"],
        "channel_sub_id": f"sub-{order['id']}",
    })
    wh = await client.post(
        "/billing/webhooks/mock",
        content=body,
        headers={"X-Billing-Signature": sign(body)},
    )
    assert wh.status_code == 200, wh.text
    return order
