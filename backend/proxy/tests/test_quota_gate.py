"""平台兜底额度闸门（Billing v1）：模型准入 / 月度额度 / 记账 / 自带 Key 通道豁免。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from eduagent_shared.config import get_settings

from app.services.pricing import cost_cents
from app.services.quota import current_period_start
from tests.conftest import TEST_USER_ID, chat_payload

PLATFORM_KEY = "sk-platform-deepseek-key"

EXPECTED_COST = cost_cents("deepseek", "deepseek-chat", 500_000, 500_000)

DEEPSEEK_COMPLETION = {
    "id": "chatcmpl-platform",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "deepseek-chat",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "2"},
            "finish_reason": "stop",
        }
    ],
    # 500k+500k tokens @ deepseek-chat 价 ≈ 68¢，足以为记账断言提供非零成本
    "usage": {"prompt_tokens": 500_000, "completion_tokens": 500_000, "total_tokens": 1_000_000},
}


@pytest.fixture
def platform_env(monkeypatch):
    monkeypatch.setenv("PLATFORM_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("PLATFORM_LLM_API_KEY", PLATFORM_KEY)
    monkeypatch.setenv("PLATFORM_LLM_MODEL", "deepseek-chat")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _ok_upstream(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=DEEPSEEK_COMPLETION)


async def test_quota_exceeded_returns_402(make_client, quota_store, platform_env):
    """月度额度用尽 → 402 quota_exceeded，且请求不触达上游厂商。"""
    quota = await quota_store.ensure_quota_period(
        TEST_USER_ID, current_period_start(), 300
    )
    quota.used_cents = 300  # 恰好用尽

    def upstream(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应被调用
        raise AssertionError("额度耗尽时不得转发上游")

    client = make_client(upstream)
    resp = await client.post("/proxy/chat", json=chat_payload())

    assert resp.status_code == 402
    body = resp.json()["error"]
    assert body["code"] == "quota_exceeded"
    assert PLATFORM_KEY not in resp.text


async def test_model_not_allowed_returns_403(make_client, quota_store, platform_env, monkeypatch):
    """平台型号不在套餐准入列表 → 403 model_not_allowed。"""
    quota_store.plans["free"].allowed_models = ["gpt-4o-mini"]  # 不含 deepseek-chat

    def upstream(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应被调用
        raise AssertionError("模型禁入时不得转发上游")

    client = make_client(upstream)
    resp = await client.post("/proxy/chat", json=chat_payload())

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "model_not_allowed"


async def test_platform_call_charges_quota(make_client, quota_store, platform_env):
    """兜底调用成功后按实际成本扣减额度（deepseek-chat 500k+500k ≈ 69¢）。"""
    client = make_client(_ok_upstream)
    resp = await client.post("/proxy/chat", json=chat_payload())

    assert resp.status_code == 200
    quota = await quota_store.get_quota_period(TEST_USER_ID, current_period_start())
    assert quota is not None
    assert quota.quota_cents == 300  # free 档惰性创建
    assert quota.used_cents == EXPECTED_COST


async def test_pro_subscription_unlocks_quota_and_models(
    make_client, quota_store, platform_env
):
    """付费订阅：额度按 pro 档对齐，模型准入放开（空列表 = 全开放）。"""
    quota_store.seed_plan("pro", "Pro 会员", 3000, [])
    quota_store.seed_subscription(
        TEST_USER_ID, "pro", datetime.now(timezone.utc) + timedelta(days=30)
    )
    # 预置一个已超 free 档的用量：pro 生效后应照常放行
    quota = await quota_store.ensure_quota_period(
        TEST_USER_ID, current_period_start(), 300
    )
    quota.used_cents = 500

    client = make_client(_ok_upstream)
    resp = await client.post("/proxy/chat", json=chat_payload())

    assert resp.status_code == 200
    quota = await quota_store.get_quota_period(TEST_USER_ID, current_period_start())
    assert quota.quota_cents == 3000  # 升级当月即时对齐 pro 额度
    assert quota.used_cents == 500 + EXPECTED_COST


async def test_expired_subscription_falls_back_to_free(
    make_client, quota_store, platform_env
):
    """订阅已过期 → 按 free 档判定（proxy 只读不写订阅状态）。"""
    quota_store.seed_plan("pro", "Pro 会员", 3000, [])
    quota_store.seed_subscription(
        TEST_USER_ID, "pro", datetime.now(timezone.utc) - timedelta(days=1)
    )
    quota = await quota_store.ensure_quota_period(
        TEST_USER_ID, current_period_start(), 300
    )
    quota.used_cents = 300  # free 档已用尽

    client = make_client(_ok_upstream)
    resp = await client.post("/proxy/chat", json=chat_payload())

    assert resp.status_code == 402


async def test_user_key_channel_bypasses_quota(
    make_client, seed_key, quota_store, platform_env
):
    """自带 Key 通道完全不受会员额度影响（额度耗尽也照常转发）。"""
    await seed_key(provider="openai")
    quota = await quota_store.ensure_quota_period(
        TEST_USER_ID, current_period_start(), 300
    )
    quota.used_cents = 300  # 平台额度已尽，与用户 Key 通道无关

    captured: dict = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=DEEPSEEK_COMPLETION)

    client = make_client(upstream)
    resp = await client.post("/proxy/chat", json=chat_payload())

    assert resp.status_code == 200
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    # 平台额度不被用户 Key 通道消耗
    quota = await quota_store.get_quota_period(TEST_USER_ID, current_period_start())
    assert quota.used_cents == 300


async def test_other_user_quota_isolated(make_client, quota_store, platform_env):
    """额度按用户隔离：他人耗尽不影响本用户。"""
    other = uuid.UUID("33333333-3333-3333-3333-333333333333")
    other_quota = await quota_store.ensure_quota_period(
        other, current_period_start(), 300
    )
    other_quota.used_cents = 300

    client = make_client(_ok_upstream)
    resp = await client.post("/proxy/chat", json=chat_payload())

    assert resp.status_code == 200
