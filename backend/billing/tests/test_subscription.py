"""GET /billing/subscription：免费默认 / 付费订阅 / 惰性过期。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.conftest import TEST_USER_ID, create_paid_order, user_headers


async def test_default_free_subscription(client, store):
    """无订阅用户：status=free，额度按 free 档惰性创建。"""
    resp = await client.get("/billing/subscription", headers=user_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "free"
    assert body["plan"]["id"] == "free"
    assert body["quota"]["quota_cents"] == 300
    assert body["quota"]["used_cents"] == 0
    assert body["quota"]["remaining_cents"] == 300


async def test_subscription_requires_user_id(client):
    from tests.conftest import INTERNAL_HEADERS

    resp = await client.get("/billing/subscription", headers=INTERNAL_HEADERS)
    assert resp.status_code == 401


async def test_paid_subscription_status(client, store):
    """支付成功后：status=active，额度对齐 pro 档，is_paid 同步。"""
    await create_paid_order(client, store)

    resp = await client.get("/billing/subscription", headers=user_headers())

    body = resp.json()
    assert body["status"] == "active"
    assert body["plan"]["id"] == "pro"
    assert body["channel"] == "mock"
    assert body["quota"]["quota_cents"] == 3000
    assert body["current_period_end"] is not None
    assert store.user_paid[TEST_USER_ID] is True


async def test_lazy_expiry(client, store):
    """过期的 active 订阅在读取时惰性过期：status 回落 free 且 is_paid 置 False。"""
    now = datetime.now(timezone.utc)
    sub = await store.create_subscription(
        user_id=TEST_USER_ID,
        plan_id="pro",
        status="active",
        channel="mock",
        current_period_start=now - timedelta(days=31),
        current_period_end=now - timedelta(days=1),
    )
    store.user_paid[TEST_USER_ID] = True

    resp = await client.get("/billing/subscription", headers=user_headers())

    body = resp.json()
    assert body["status"] == "free"
    assert store.subs[sub.id].status == "expired"
    assert store.user_paid[TEST_USER_ID] is False
