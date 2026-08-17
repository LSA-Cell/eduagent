"""降级策略：自有模型失败 → 按条件切 external；不允许时返回 OpenAI 错误格式。"""

from __future__ import annotations

import httpx

from tests.conftest import COMPLETION_EXTERNAL


async def test_failover_to_external_on_connect_error(make_client):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "vllm-general":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json=COMPLETION_EXTERNAL)

    async with make_client(handler) as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "edu-general-14b",
                "messages": [{"role": "user", "content": "hi"}],
                "user_id": "u-1",
                "auto_failover": True,
            },
        )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "来自外部 API"
    assert calls == ["vllm-general", "proxy"]


async def test_no_failover_when_auto_failover_false(make_client):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        raise httpx.ConnectError("connection refused", request=request)

    async with make_client(handler) as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "edu-general-14b",
                "messages": [{"role": "user", "content": "hi"}],
                "user_id": "u-1",
                "auto_failover": False,
            },
        )
    assert resp.status_code == 502
    body = resp.json()
    assert body["error"]["type"] == "server_error"
    assert calls == ["vllm-general"]  # 未触碰 external


async def test_internal_source_never_failover(make_client):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "vllm-general":
            return httpx.Response(500, json={"error": "cuda oom"})
        return httpx.Response(200, json=COMPLETION_EXTERNAL)

    async with make_client(handler) as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "edu-general-14b",
                "messages": [{"role": "user", "content": "hi"}],
                "inference_source": "internal",
                "user_id": "u-1",
            },
        )
    assert resp.status_code == 502
    assert "error" in resp.json()
    assert calls == ["vllm-general"]  # 显式 internal 不降级


async def test_external_source_goes_directly_to_proxy(make_client):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(200, json=COMPLETION_EXTERNAL)

    async with make_client(handler) as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "provider": "openai",
                "messages": [{"role": "user", "content": "hi"}],
                "inference_source": "external",
                "user_id": "u-1",
            },
        )
    assert resp.status_code == 200
    assert calls == ["proxy"]
