"""GET /billing/plans：套餐列表与内部令牌守卫。"""

from __future__ import annotations

from tests.conftest import INTERNAL_HEADERS


async def test_list_plans(client):
    resp = await client.get("/billing/plans", headers=INTERNAL_HEADERS)

    assert resp.status_code == 200
    plans = resp.json()
    assert [p["id"] for p in plans] == ["free", "pro", "teacher_pro"]
    assert plans[1]["price_cents"] == 6800
    assert plans[1]["monthly_quota_cents"] == 3000


async def test_plans_require_internal_token(client):
    resp = await client.get("/billing/plans")
    assert resp.status_code == 403


async def test_inactive_plan_hidden(client, store):
    store.plans["teacher_pro"].is_active = False

    resp = await client.get("/billing/plans", headers=INTERNAL_HEADERS)

    assert [p["id"] for p in resp.json()] == ["free", "pro"]
