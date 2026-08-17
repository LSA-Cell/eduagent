"""gemini 通道：generateContent / streamGenerateContent 适配 → OpenAI 格式转换。"""

from __future__ import annotations

import json

import httpx

from tests.conftest import (
    PLAINTEXT_KEY,
    StaticByteStream,
    chat_payload,
    used_budget_of,
)

GEMINI_MODEL = "gemini-2.5-flash"

# gemini-2.5-flash：0.30/2.50 USD/1M → (400000*0.3 + 40000*2.5)/1e6 = 0.22 USD = 22 美分
GEMINI_COMPLETION = {
    "candidates": [
        {
            "content": {"parts": [{"text": "2"}], "role": "model"},
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 400000,
        "candidatesTokenCount": 40000,
        "totalTokenCount": 440000,
    },
}

GEMINI_SSE = (
    'data: {"candidates": [{"content": {"parts": [{"text": "答案"}], "role": "model"}}]}\n\n'
    'data: {"candidates": [{"content": {"parts": [{"text": "是 2"}], "role": "model"}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 400000, "candidatesTokenCount": 40000, "totalTokenCount": 440000}}\n\n'
).encode()


async def test_gemini_non_stream_converts_to_openai(make_client, seed_key, key_store):
    await seed_key(provider="gemini")
    captured: dict = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["x-goog-api-key"] = request.headers.get("x-goog-api-key")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=GEMINI_COMPLETION)

    client = make_client(upstream)
    resp = await client.post(
        "/proxy/chat",
        json=chat_payload(provider="gemini", model=GEMINI_MODEL, max_tokens=256),
    )

    assert resp.status_code == 200
    # 请求构造：generateContent 端点 / x-goog-api-key / systemInstruction / generationConfig
    assert (
        captured["url"]
        == f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    )
    assert captured["x-goog-api-key"] == PLAINTEXT_KEY
    assert captured["body"]["contents"] == [
        {"role": "user", "parts": [{"text": "1+1=?"}]}
    ]
    assert captured["body"]["systemInstruction"] == {
        "parts": [{"text": "你是数学老师"}]
    }
    assert captured["body"]["generationConfig"] == {"maxOutputTokens": 256}
    # 响应转换：OpenAI chat.completion 形态
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == GEMINI_MODEL
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "2"}
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"] == {
        "prompt_tokens": 400000,
        "completion_tokens": 40000,
        "total_tokens": 440000,
    }
    assert await used_budget_of(key_store, provider="gemini") == 22
    assert PLAINTEXT_KEY not in resp.text


async def test_gemini_stream_converts_to_openai_chunks(make_client, seed_key, key_store):
    """streamGenerateContent?alt=sse 的分段响应 → chat.completion.chunk 序列。"""
    await seed_key(provider="gemini")
    captured: dict = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            stream=StaticByteStream(GEMINI_SSE),
            headers={"content-type": "text/event-stream"},
        )

    client = make_client(upstream)
    async with client.stream(
        "POST",
        "/proxy/chat",
        json=chat_payload(provider="gemini", model=GEMINI_MODEL, stream=True),
    ) as resp:
        assert resp.status_code == 200
        text = (await resp.aread()).decode()

    assert captured["url"].endswith(f"{GEMINI_MODEL}:streamGenerateContent?alt=sse")

    lines = [line for line in text.splitlines() if line.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    chunks = [json.loads(line[len("data: "):]) for line in lines[:-1]]

    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
    deltas = [
        c["choices"][0]["delta"].get("content", "")
        for c in chunks
        if c["choices"] and "content" in c["choices"][0]["delta"]
    ]
    assert "".join(deltas) == "答案是 2"
    finish = [c for c in chunks if c["choices"] and c["choices"][0]["finish_reason"]]
    assert finish[-1]["choices"][0]["finish_reason"] == "stop"
    usage_chunks = [c for c in chunks if c["choices"] == []]
    assert len(usage_chunks) == 1
    assert usage_chunks[0]["usage"]["total_tokens"] == 440000
    assert await used_budget_of(key_store, provider="gemini") == 22
    assert PLAINTEXT_KEY not in text
