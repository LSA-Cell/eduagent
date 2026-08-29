"""BFF 测试 fixtures：全部使用内存仓储 + fakeredis，无需外部服务。"""

from __future__ import annotations

import fakeredis.aioredis
import httpx
import pytest

from app import deps
from app.main import create_app
from app.ratelimit import SlidingWindowLimiter
from app.services.store import (
    InMemoryApiKeyStore,
    InMemoryChatStore,
    InMemoryUserStore,
)


async def _free_tier_resolver(user_id: str) -> str:
    return "free"


@pytest.fixture
def user_store() -> InMemoryUserStore:
    return InMemoryUserStore()


@pytest.fixture
def api_key_store() -> InMemoryApiKeyStore:
    return InMemoryApiKeyStore()


@pytest.fixture
def chat_store() -> InMemoryChatStore:
    return InMemoryChatStore()


@pytest.fixture
async def app(user_store, api_key_store, chat_store):
    application = create_app()
    application.dependency_overrides[deps.get_user_store] = lambda: user_store
    application.dependency_overrides[deps.get_api_key_store] = lambda: api_key_store
    application.dependency_overrides[deps.get_chat_store] = lambda: chat_store

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    application.state.redis = fake_redis
    application.state.rate_limiter = SlidingWindowLimiter(fake_redis)
    application.state.rate_limits = {"free": 20, "paid": 60, "teacher": 100}
    application.state.rate_tier_resolver = _free_tier_resolver
    application.state.http_client = httpx.AsyncClient()
    yield application
    await application.state.http_client.aclose()
    await fake_redis.aclose()


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def register_and_login(client: httpx.AsyncClient, phone: str = "13800000001") -> str:
    """注册并返回 access_token（注册需短信验证码，dev 环境走 dev_code 直通）。"""
    code_resp = await client.post(
        "/api/v1/auth/sms-code", json={"phone": phone, "scene": "register"}
    )
    assert code_resp.status_code == 200, code_resp.text
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "phone": phone,
            "password": "password123",
            "sms_code": code_resp.json()["dev_code"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["tokens"]["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
