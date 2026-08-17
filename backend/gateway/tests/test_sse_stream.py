"""SSE 流式透传与首 token 超时。"""

from __future__ import annotations

import asyncio

import httpx

from tests.conftest import SSE_EXTERNAL, SSE_INTERNAL, StaticByteStream


async def test_sse_stream_passthrough(make_client):
    """vLLM 的 SSE chunk 应逐段透传到调用方。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=StaticByteStream(SSE_INTERNAL),
            headers={"content-type": "text/event-stream"},
        )

    async with make_client(handler) as client:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "edu-general-14b",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            chunks = [chunk async for chunk in resp.aiter_raw()]

    body = b"".join(chunks)
    assert 'delta":{"content":"你"}'.encode() in body
    assert 'delta":{"content":"好"}'.encode() in body
    assert body.rstrip().endswith(b"data: [DONE]")


class _SlowStream(httpx.AsyncByteStream):
    """首 chunk 延迟 0.2s，超过测试中 0.05s 的首 token 超时。"""

    async def __aiter__(self):
        await asyncio.sleep(0.2)
        yield SSE_INTERNAL


async def test_first_token_timeout_triggers_failover(make_client):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "vllm-general":
            return httpx.Response(
                200, stream=_SlowStream(), headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(
            200,
            stream=StaticByteStream(SSE_EXTERNAL),
            headers={"content-type": "text/event-stream"},
        )

    async with make_client(handler) as client:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "edu-general-14b",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
                "user_id": "u-1",
                "auto_failover": True,
            },
        ) as resp:
            assert resp.status_code == 200
            body = b"".join([chunk async for chunk in resp.aiter_raw()])

    # 首 token 超时后降级到 external，响应体来自 proxy
    assert "外部".encode() in body
    assert "vllm-general" in calls and "proxy" in calls
