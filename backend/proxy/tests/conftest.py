"""Proxy 测试 fixtures：httpx MockTransport 模拟上游厂商，InMemory 仓储替代 PostgreSQL。

Key 均以真实 AES-256-GCM 加密后入"库"，覆盖 加密 → 解密 → 转发 全链路；全程离线。
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app import deps
from app.main import create_app
from app.services.quota import InMemoryQuotaStore
from app.services.store import InMemoryApiKeyStore

from eduagent_shared.security.crypto import encrypt_key

TEST_USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
PLAINTEXT_KEY = "sk-test-plaintext-key-0001"

INTERNAL_HEADERS = {"X-Internal-Token": "test-internal-token"}


class StaticByteStream(httpx.AsyncByteStream):
    """一次性字节流：模拟真实上游的流式响应（httpx.Response(content=...) 构造的
    响应是"已消费"状态，无法再 aiter_lines，流式用例必须用 stream= 传入）。"""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self):
        yield self._data


def chat_payload(
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    stream: bool = False,
    max_tokens: int | None = None,
    user_id: str | None = None,
    content: str = "1+1=?",
) -> dict:
    body: dict = {
        "user_id": user_id or str(TEST_USER_ID),
        "provider": provider,
        "model": model,
        "messages": [
            {"role": "system", "content": "你是数学老师"},
            {"role": "user", "content": content},
        ],
        "stream": stream,
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    return body


@pytest.fixture
def key_store() -> InMemoryApiKeyStore:
    return InMemoryApiKeyStore()


@pytest.fixture
def seed_key(key_store):
    """工厂：为用户绑定加密 Key（真实 AES-256-GCM 加密）。"""

    async def seed(
        provider: str = "openai",
        plaintext: str = PLAINTEXT_KEY,
        monthly_budget: int | None = None,
        used_budget: int = 0,
        is_active: bool = True,
        user_id: uuid.UUID = TEST_USER_ID,
    ):
        return await key_store.create(
            user_id=user_id,
            provider=provider,
            encrypted_key=encrypt_key(plaintext),
            monthly_budget=monthly_budget,
            used_budget=used_budget,
            is_active=is_active,
        )

    return seed


@pytest.fixture
def quota_store() -> InMemoryQuotaStore:
    """额度闸门仓储（默认 seed free 档：300¢ 额度 + 低成本模型准入）。"""
    return InMemoryQuotaStore()


@pytest.fixture
def app(key_store, quota_store):
    application = create_app()
    application.dependency_overrides[deps.get_key_store] = lambda: key_store
    application.dependency_overrides[deps.get_quota_store] = lambda: quota_store
    application.state.http_client = httpx.AsyncClient()
    return application


@pytest.fixture
async def make_client(app):
    """工厂：按上游 handler 构造测试客户端（MockTransport 注入为代理的 http_client）。"""
    clients: list[httpx.AsyncClient] = []

    def factory(handler) -> httpx.AsyncClient:
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        app.state.http_client = upstream
        downstream = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers=INTERNAL_HEADERS,
        )
        clients.extend([upstream, downstream])
        return downstream

    yield factory
    for c in clients:
        await c.aclose()


async def used_budget_of(
    key_store: InMemoryApiKeyStore,
    user_id: uuid.UUID = TEST_USER_ID,
    provider: str = "openai",
) -> int:
    records = await key_store.list_for_user(user_id)
    return next(r.used_budget for r in records if r.provider == provider)
