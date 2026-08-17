"""claude 通道：Anthropic 请求构造、非流式/流式 → OpenAI 格式转换。"""

from __future__ import annotations

import json

import httpx

from tests.conftest import (
    PLAINTEXT_KEY,
    StaticByteStream,
    chat_payload,
    used_budget_of,
)

CLAUDE_MODEL = "claude-sonnet-4-20250514"

# claude-sonnet-4：3.00/15.00 USD/1M → (100000*3 + 10000*15)/1e6 = 0.45 USD = 45 美分
CLAUDE_COMPLETION = {
    "id": "msg_01X",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "答案是 2"}],
    "model": CLAUDE_MODEL,
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 100000, "output_tokens": 10000},
}

CLAUDE_SSE = (
    "event: message_start\n"
    'data: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","content":[],"model":"'
    + CLAUDE_MODEL
    + '","stop_reason":null,"usage":{"input_tokens":100000,"output_tokens":1}}}\n\n'
    "event: content_block_start\n"
    'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
    "event: content_block_delta\n"
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"答案"}}\n\n'
    "event: content_block_delta\n"
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"是 2"}}\n\n'
    "event: message_delta\n"
    'data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":10000}}\n\n'
    "event: message_stop\n"
    'data: {"type":"message_stop"}\n\n'
).encode()


async def test_claude_non_stream_converts_to_openai(make_client, seed_key, key_store):
    await seed_key(provider="claude")
    captured: dict = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["x-api-key"] = request.headers.get("x-api-key")
        captured["anthropic-version"] = request.headers.get("anthropic-version")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=CLAUDE_COMPLETION)

    client = make_client(upstream)
    resp = await client.post(
        "/proxy/chat", json=chat_payload(provider="claude", model=CLAUDE_MODEL)
    )

    assert resp.status_code == 200
    # 请求构造：端点 / 鉴权头 / system 提取 / max_tokens 默认值
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["x-api-key"] == PLAINTEXT_KEY
    assert captured["anthropic-version"] == "2023-06-01"
    assert captured["body"]["system"] == "你是数学老师"
    assert captured["body"]["messages"] == [{"role": "user", "content": "1+1=?"}]
    assert captured["body"]["max_tokens"] == 1024
    # 响应转换：OpenAI chat.completion 形态
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == CLAUDE_MODEL
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "答案是 2"}
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"] == {
        "prompt_tokens": 100000,
        "completion_tokens": 10000,
        "total_tokens": 110000,
    }
    assert await used_budget_of(key_store, provider="claude") == 45
    assert PLAINTEXT_KEY not in resp.text


async def test_claude_max_tokens_passthrough(make_client, seed_key):
    await seed_key(provider="claude")
    captured: dict = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=CLAUDE_COMPLETION)

    client = make_client(upstream)
    resp = await client.post(
        "/proxy/chat",
        json=chat_payload(provider="claude", model=CLAUDE_MODEL, max_tokens=777),
    )
    assert resp.status_code == 200
    assert captured["body"]["max_tokens"] == 777


async def test_claude_stream_converts_to_openai_chunks(make_client, seed_key, key_store):
    """message_start/content_block_delta/message_delta → chat.completion.chunk 序列。"""
    await seed_key(provider="claude")

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=StaticByteStream(CLAUDE_SSE),
            headers={"content-type": "text/event-stream"},
        )

    client = make_client(upstream)
    async with client.stream(
        "POST",
        "/proxy/chat",
        json=chat_payload(provider="claude", model=CLAUDE_MODEL, stream=True),
    ) as resp:
        assert resp.status_code == 200
        text = (await resp.aread()).decode()

    lines = [line for line in text.splitlines() if line.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    chunks = [json.loads(line[len("data: "):]) for line in lines[:-1]]

    # 首 chunk 下发角色
    assert chunks[0]["object"] == "chat.completion.chunk"
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
    # 文本增量
    deltas = [
        c["choices"][0]["delta"].get("content", "")
        for c in chunks
        if c["choices"] and "content" in c["choices"][0]["delta"]
    ]
    assert "".join(deltas) == "答案是 2"
    # finish chunk
    finish = [c for c in chunks if c["choices"] and c["choices"][0]["finish_reason"]]
    assert finish[-1]["choices"][0]["finish_reason"] == "stop"
    # 末尾补发的 usage chunk（choices 为空）
    usage_chunks = [c for c in chunks if c["choices"] == []]
    assert len(usage_chunks) == 1
    assert usage_chunks[0]["usage"] == {
        "prompt_tokens": 100000,
        "completion_tokens": 10000,
        "total_tokens": 110000,
    }
    assert await used_budget_of(key_store, provider="claude") == 45
    assert PLAINTEXT_KEY not in text
