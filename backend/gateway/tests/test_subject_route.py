"""学科路由：subject=math/physics/chemistry 优先学科模型。"""

from __future__ import annotations

import json

import httpx

from tests.conftest import COMPLETION_GENERAL


async def test_subject_math_routes_to_math_model(make_client):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        body = json.loads(request.content)
        assert request.url.host == "vllm-math"  # 学科模型实例
        assert body["model"] == "edu-math-7b"  # model 已按学科改写
        return httpx.Response(200, json=COMPLETION_GENERAL)

    async with make_client(handler) as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "edu-general-14b",
                "subject": "math",
                "messages": [{"role": "user", "content": "证明勾股定理"}],
            },
        )
    assert resp.status_code == 200
    assert len(calls) == 1


async def test_unknown_subject_falls_back_to_requested_model(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "vllm-general"  # 未命中学科映射 → 保持请求模型
        return httpx.Response(200, json=COMPLETION_GENERAL)

    async with make_client(handler) as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "edu-general-14b",
                "subject": "art",
                "messages": [{"role": "user", "content": "讲讲文艺复兴"}],
            },
        )
    assert resp.status_code == 200
