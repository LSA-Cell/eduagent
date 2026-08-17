"""internal 路由：模型命中 VLLM_ROUTES，扩展字段剥离，非流式透传。"""

from __future__ import annotations

import json

import httpx

from tests.conftest import COMPLETION_GENERAL


async def test_internal_route_non_stream(make_client):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        body = json.loads(request.content)
        assert request.url.host == "vllm-general"
        assert body["model"] == "edu-general-14b"
        # 扩展字段不透传给 vLLM
        for ext in ("subject", "inference_source", "user_id", "provider", "auto_failover"):
            assert ext not in body
        return httpx.Response(200, json=COMPLETION_GENERAL)

    async with make_client(handler) as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "edu-general-14b",
                "messages": [{"role": "user", "content": "你好"}],
                "user_id": "u-1",
                "subject": "chinese",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "来自自有模型"
    assert len(calls) == 1


async def test_requires_internal_token(make_client):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, json=COMPLETION_GENERAL)

    async with make_client(handler) as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "edu-general-14b", "messages": [{"role": "user", "content": "hi"}]},
            headers={"X-Internal-Token": "wrong-token"},
        )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["type"] == "authentication_error"


async def test_models_endpoint(make_client):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, json=COMPLETION_GENERAL)

    async with make_client(handler) as client:
        resp = await client.get("/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert body["data"][0]["id"] == "edu-general-14b"
