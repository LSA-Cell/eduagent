"""GET /proxy/usage/{user_id} 与 /health。"""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from tests.conftest import OTHER_USER_ID, PLAINTEXT_KEY, TEST_USER_ID


async def test_usage_listing_per_provider(make_client, seed_key):
    await seed_key(provider="openai", monthly_budget=100, used_budget=6)
    await seed_key(provider="claude", monthly_budget=None, used_budget=45)
    await seed_key(provider="gemini", user_id=OTHER_USER_ID)  # 其他用户，不应出现

    client = make_client(lambda request: httpx.Response(500))
    resp = await client.get(f"/proxy/usage/{TEST_USER_ID}")

    assert resp.status_code == 200
    items = {item["provider"]: item for item in resp.json()}
    assert set(items) == {"openai", "claude"}
    assert items["openai"] == {
        "provider": "openai",
        "monthly_budget": 100,
        "used_budget": 6,
        "remaining_budget": 94,
        "is_active": True,
    }
    assert items["claude"]["monthly_budget"] is None
    assert items["claude"]["remaining_budget"] is None
    assert items["claude"]["used_budget"] == 45
    # 安全红线：用量接口不含任何 Key 信息
    for item in resp.json():
        assert "key" not in " ".join(item.keys()).lower()
    assert PLAINTEXT_KEY not in resp.text


async def test_usage_other_user_empty(make_client, seed_key):
    await seed_key(provider="openai")
    client = make_client(lambda request: httpx.Response(500))
    resp = await client.get(f"/proxy/usage/{OTHER_USER_ID}")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_usage_requires_internal_token(app: FastAPI):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/proxy/usage/{TEST_USER_ID}")
    assert resp.status_code == 403


async def test_health(app: FastAPI):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
