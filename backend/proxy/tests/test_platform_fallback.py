"""平台级统一 Key 兜底：用户未绑 Key 时由平台 Key 代付转发（PLATFORM_LLM_* 驱动）。"""

from __future__ import annotations

import json

import httpx
import pytest

from eduagent_shared.config import get_settings

from tests.conftest import PLAINTEXT_KEY, TEST_USER_ID, chat_payload

PLATFORM_KEY = "sk-platform-deepseek-key"

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
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}


@pytest.fixture
def platform_env(monkeypatch):
    """配置平台兜底 Key 并刷新配置缓存；用例结束自动还原环境与缓存。"""
    monkeypatch.setenv("PLATFORM_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("PLATFORM_LLM_API_KEY", PLATFORM_KEY)
    monkeypatch.setenv("PLATFORM_LLM_MODEL", "deepseek-chat")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_platform_fallback_when_user_has_no_key(
    make_client, key_store, platform_env
):
    """用户未绑任何 Key + 平台兜底开启 → 平台 Key 转发到 deepseek，型号用平台默认值。"""
    captured: dict = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=DEEPSEEK_COMPLETION)

    client = make_client(upstream)
    # 用户请求的是 openai/gpt-4o-mini，但未绑任何 Key → 兜底到平台 deepseek
    resp = await client.post("/proxy/chat", json=chat_payload())

    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "2"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == f"Bearer {PLATFORM_KEY}"
    assert captured["body"]["model"] == "deepseek-chat"
    # 平台通道无 key 行可记账：不应凭空产生用户 Key 记录，响应体不回显平台 Key
    assert await key_store.list_for_user(TEST_USER_ID) == []
    assert PLATFORM_KEY not in resp.text


async def test_platform_fallback_stream(make_client, key_store, platform_env):
    """流式兜底：正常输出 chunk 与 [DONE]，且不向用户 Key 记账。"""
    from tests.conftest import StaticByteStream

    sse = (
        'data: {"id":"c1","object":"chat.completion.chunk","created":1700000000,"model":"deepseek-chat","choices":[{"index":0,"delta":{"content":"答案"},"finish_reason":null}]}\n\n'
        'data: {"id":"c1","object":"chat.completion.chunk","created":1700000000,"model":"deepseek-chat","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    ).encode()

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=StaticByteStream(sse),
            headers={"content-type": "text/event-stream"},
        )

    client = make_client(upstream)
    async with client.stream("POST", "/proxy/chat", json=chat_payload(stream=True)) as resp:
        assert resp.status_code == 200
        text = (await resp.aread()).decode()

    lines = [line for line in text.splitlines() if line.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    assert "答案" in text
    assert PLATFORM_KEY not in text
    assert await key_store.list_for_user(TEST_USER_ID) == []


async def test_user_key_takes_precedence_over_platform(
    make_client, seed_key, key_store, platform_env
):
    """用户已绑自有 Key 时仍走用户通道（用户自担成本），不触发平台兜底。"""
    await seed_key(provider="openai")
    captured: dict = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=DEEPSEEK_COMPLETION)

    client = make_client(upstream)
    resp = await client.post("/proxy/chat", json=chat_payload())

    assert resp.status_code == 200
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["authorization"] == f"Bearer {PLAINTEXT_KEY}"
    assert captured["body"]["model"] == "gpt-4o-mini"


async def test_platform_triplet_partial_config_rejected(monkeypatch):
    """三项配置只填一部分时拒绝启动（防半配状态上线）。"""
    monkeypatch.setenv("PLATFORM_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("PLATFORM_LLM_API_KEY", "")
    monkeypatch.setenv("PLATFORM_LLM_MODEL", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="同时配置"):
            get_settings()
    finally:
        get_settings.cache_clear()
