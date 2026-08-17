"""额度追踪：预算超限 402、usage 累加、无 usage 时字符估算、价格表/估算函数单测。"""

from __future__ import annotations

import httpx

from app.services.pricing import (
    PRICE_TABLE,
    cost_cents,
    estimate_tokens,
    price_for,
)
from tests.conftest import chat_payload, used_budget_of
from tests.test_chat_openai import OPENAI_COMPLETION


async def test_budget_exceeded_returns_402_before_upstream(make_client, seed_key):
    """used_budget 已达 monthly_budget：直接 402，不触碰上游。"""
    await seed_key(provider="openai", monthly_budget=6, used_budget=6)
    called = False

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=OPENAI_COMPLETION)

    client = make_client(upstream)
    resp = await client.post("/proxy/chat", json=chat_payload())
    assert resp.status_code == 402
    assert resp.json()["error"]["code"] == "monthly_budget_exceeded"
    assert called is False


async def test_budget_accumulates_until_limit(make_client, seed_key, key_store):
    """首次调用记账 6 美分后恰好达到上限，第二次 402。"""
    await seed_key(provider="openai", monthly_budget=6)
    client = make_client(lambda request: httpx.Response(200, json=OPENAI_COMPLETION))

    first = await client.post("/proxy/chat", json=chat_payload())
    assert first.status_code == 200
    assert await used_budget_of(key_store) == 6

    second = await client.post("/proxy/chat", json=chat_payload())
    assert second.status_code == 402


async def test_no_budget_limit(make_client, seed_key):
    """monthly_budget 为 NULL 表示不限额。"""
    await seed_key(provider="openai", monthly_budget=None, used_budget=999999)
    client = make_client(lambda request: httpx.Response(200, json=OPENAI_COMPLETION))
    resp = await client.post("/proxy/chat", json=chat_payload())
    assert resp.status_code == 200


async def test_estimate_tokens_when_usage_missing(make_client, seed_key, key_store):
    """上游未返回 usage：按字符数估算 token 后折算记账。"""
    await seed_key(provider="openai")
    completion_no_usage = {
        "id": "chatcmpl-no-usage",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "好"},
                "finish_reason": "stop",
            }
        ],
    }
    client = make_client(lambda request: httpx.Response(200, json=completion_no_usage))

    prompt_content = "a" * 400_000
    resp = await client.post(
        "/proxy/chat", json=chat_payload(model="gpt-4o", content=prompt_content)
    )
    assert resp.status_code == 200
    expected = cost_cents(
        "openai",
        "gpt-4o",
        estimate_tokens("你是数学老师" + prompt_content),
        estimate_tokens("好"),
    )
    assert expected > 0  # 大提示词估算出约 10 万 token → 25 美分
    assert expected == 25
    assert await used_budget_of(key_store) == expected


# ---------------------------------------------------------------------------
# 价格表 / 估算函数单测
# ---------------------------------------------------------------------------


def test_price_prefix_match_and_fallback():
    assert price_for("openai", "gpt-4o-2024-08-06") == PRICE_TABLE["openai"]["gpt-4o"]
    assert price_for("openai", "gpt-4o-mini-2024-07-18") == PRICE_TABLE["openai"]["gpt-4o-mini"]
    assert price_for("openai", "some-future-model") == PRICE_TABLE["openai"]["default"]
    assert price_for("unknown-provider", "x") == {"input": 1.00, "output": 3.00}


def test_cost_cents_exact_values():
    assert cost_cents("openai", "gpt-4o", 1_000_000, 0) == 250
    assert cost_cents("openai", "gpt-4o-mini", 200_000, 50_000) == 6
    assert cost_cents("claude", "claude-sonnet-4-20250514", 100_000, 10_000) == 45
    assert cost_cents("gemini", "gemini-2.5-flash", 400_000, 40_000) == 22
    assert cost_cents("deepseek", "deepseek-chat", 0, 0) == 0


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abc") == 1  # 不足 4 字符按 1 计
    assert estimate_tokens("a" * 400) == 100
