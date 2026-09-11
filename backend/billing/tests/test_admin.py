"""POST /billing/admin/grant：手工开通（客服/测试链路）。"""

from __future__ import annotations

from tests.conftest import INTERNAL_HEADERS, TEST_USER_ID, user_headers


async def test_admin_grant_activates(client, store):
    resp = await client.post(
        "/billing/admin/grant",
        json={"user_id": str(TEST_USER_ID), "plan_id": "pro", "days": 30},
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["granted"] is True
    assert body["plan_id"] == "pro"
    sub = await store.get_active_subscription(TEST_USER_ID)
    assert sub is not None and sub.channel == "admin"
    assert store.user_paid[TEST_USER_ID] is True


async def test_admin_grant_idempotent_via_renewal(client, store):
    """重复 grant = 续期换新订阅：旧订阅作废，active 仍只有一条。"""
    for _ in range(2):
        resp = await client.post(
            "/billing/admin/grant",
            json={"user_id": str(TEST_USER_ID), "plan_id": "pro", "days": 30},
            headers=INTERNAL_HEADERS,
        )
        assert resp.status_code == 201
    actives = [
        s for s in store.subs.values()
        if s.user_id == TEST_USER_ID and s.status == "active"
    ]
    assert len(actives) == 1


async def test_admin_grant_unknown_plan_404(client):
    resp = await client.post(
        "/billing/admin/grant",
        json={"user_id": str(TEST_USER_ID), "plan_id": "nonexistent", "days": 30},
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 404


async def test_admin_grant_requires_internal_token(client):
    """admin 端点只认 internal token，用户身份头不能冒充。"""
    resp = await client.post(
        "/billing/admin/grant",
        json={"user_id": str(TEST_USER_ID), "plan_id": "pro", "days": 30},
        headers={"X-User-Id": str(TEST_USER_ID)},
    )
    assert resp.status_code == 403
