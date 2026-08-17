"""openai/deepseek 通道：加密→解密→转发全链路、非流式/流式、内部令牌、Key 缺失。"""

from __future__ import annotations

import json

import httpx
from fastapi import FastAPI

from tests.conftest import (
    PLAINTEXT_KEY,
    StaticByteStream,
    chat_payload,
    used_budget_of,
)

OPENAI_COMPLETION = {
    "id": "chatcmpl-upstream",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "2"},
            "finish_reason": "stop",
        }
    ],
    # gpt-4o-mini：0.15/0.60 USD/1M → (200000*0.15 + 50000*0.60)/1e6 = 0.06 USD = 6 美分
    "usage": {"prompt_tokens": 200000, "completion_tokens": 50000, "total_tokens": 250000},
}

OPENAI_SSE = (
    'data: {"id":"c1","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
    'data: {"id":"c1","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{"content":"答案是 2"},"finish_reason":null}]}\n\n'
    'data: {"id":"c1","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    'data: {"id":"c1","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o-mini","choices":[],"usage":{"prompt_tokens":200000,"completion_tokens":50000,"total_tokens":250000}}\n\n'
    "data: [DONE]\n\n"
).encode()


async def test_openai_non_stream_full_chain(make_client, seed_key, key_store):
    """加密 Key 入库 → 代理解密 → 以正确鉴权头转发 → OpenAI JSON 原样回传 → 记账。"""
    await seed_key(provider="openai")
    captured: dict = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=OPENAI_COMPLETION)

    client = make_client(upstream)
    resp = await client.post("/proxy/chat", json=chat_payload(max_tokens=128))

    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "2"
    assert body["usage"]["total_tokens"] == 250000
    # 全链路：密文入库 → 代理解密 → 转发到 OpenAI 端点
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["authorization"] == f"Bearer {PLAINTEXT_KEY}"
    assert captured["body"]["model"] == "gpt-4o-mini"
    assert captured["body"]["max_tokens"] == 128
    assert captured["body"]["stream"] is False
    # 额度记账：usage → 6 美分
    assert await used_budget_of(key_store) == 6
    # 安全红线：响应体不含明文 Key
    assert PLAINTEXT_KEY not in resp.text


async def test_deepseek_uses_deepseek_endpoint(make_client, seed_key, key_store):
    """deepseek 走 OpenAI 兼容协议，但 base_url 必须是 api.deepseek.com。"""
    await seed_key(provider="deepseek", plaintext="sk-deepseek-key")
    captured: dict = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={**OPENAI_COMPLETION, "model": "deepseek-chat"})

    client = make_client(upstream)
    resp = await client.post(
        "/proxy/chat", json=chat_payload(provider="deepseek", model="deepseek-chat")
    )

    assert resp.status_code == 200
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == "Bearer sk-deepseek-key"
    # deepseek-chat：0.27/1.10 USD/1M → (54000 + 55000)/1e6 ≈ 0.109 USD = 11 美分
    assert await used_budget_of(key_store, provider="deepseek") == 11


async def test_openai_stream_passthrough(make_client, seed_key, key_store):
    """流式：chunk 透传 + 代理主动注入 stream_options 追踪 usage，末尾 [DONE]。"""
    await seed_key(provider="openai")
    captured: dict = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            stream=StaticByteStream(OPENAI_SSE),
            headers={"content-type": "text/event-stream"},
        )

    client = make_client(upstream)
    async with client.stream("POST", "/proxy/chat", json=chat_payload(stream=True)) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        text = (await resp.aread()).decode()

    assert captured["body"]["stream"] is True
    assert captured["body"]["stream_options"] == {"include_usage": True}

    lines = [line for line in text.splitlines() if line.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    chunks = [json.loads(line[len("data: "):]) for line in lines[:-1]]
    contents = [c["choices"][0]["delta"].get("content") for c in chunks if c["choices"]]
    assert "答案是 2" in contents
    # usage chunk 随流透传（choices 为空）
    assert any(
        c["choices"] == [] and c["usage"]["total_tokens"] == 250000 for c in chunks
    )
    assert await used_budget_of(key_store) == 6
    assert PLAINTEXT_KEY not in text


async def test_missing_internal_token_rejected(app: FastAPI):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/proxy/chat", json=chat_payload())
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "invalid_internal_token"


async def test_wrong_internal_token_rejected(app: FastAPI):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Internal-Token": "wrong-token"},
    ) as client:
        resp = await client.post("/proxy/chat", json=chat_payload())
    assert resp.status_code == 403


async def test_no_key_returns_404(make_client):
    client = make_client(lambda request: httpx.Response(500))
    resp = await client.post(
        "/proxy/chat",
        json=chat_payload(provider="claude", model="claude-sonnet-4-20250514"),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "api_key_not_found"


async def test_inactive_key_returns_404(make_client, seed_key):
    await seed_key(provider="openai", is_active=False)
    client = make_client(lambda request: httpx.Response(500))
    resp = await client.post("/proxy/chat", json=chat_payload())
    assert resp.status_code == 404


async def test_unsupported_provider_returns_400(make_client):
    client = make_client(lambda request: httpx.Response(500))
    resp = await client.post("/proxy/chat", json=chat_payload(provider="foo"))
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "invalid_request_error"


async def test_upstream_4xx_passthrough_and_key_redacted(make_client, seed_key):
    """上游 4xx 原样透传状态码；错误消息中若回显 Key 必须脱敏。"""
    await seed_key(provider="openai")

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"message": f"Invalid key {PLAINTEXT_KEY} provided"}}
        )

    client = make_client(upstream)
    resp = await client.post("/proxy/chat", json=chat_payload())
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "upstream_error"
    assert PLAINTEXT_KEY not in resp.text
    assert "***" in resp.json()["error"]["message"]


async def test_upstream_5xx_mapped_to_502(make_client, seed_key):
    await seed_key(provider="openai")

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "overloaded"}})

    client = make_client(upstream)
    resp = await client.post("/proxy/chat", json=chat_payload())
    assert resp.status_code == 502
    assert resp.json()["error"]["message"] == "overloaded"


async def test_upstream_stream_error_status(make_client, seed_key):
    """流式请求上游直接返回 429：读完后按 4xx 透传，不进入流式响应。"""
    await seed_key(provider="openai")

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    client = make_client(upstream)
    resp = await client.post("/proxy/chat", json=chat_payload(stream=True))
    assert resp.status_code == 429
    assert resp.json()["error"]["message"] == "rate limited"
