"""模型列表转发测试：GET /models → 推理网关 /v1/models。"""

from __future__ import annotations

import httpx
import pytest

from app import deps
from tests.conftest import auth_headers, register_and_login


@pytest.fixture
def gateway(app):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={"object": "list", "data": [{"id": "edu-general-14b"}, {"id": "edu-math-7b"}]},
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.dependency_overrides[deps.get_http_client] = lambda: mock_client
    return calls


async def test_list_models_forwarded(client, gateway):
    token = await register_and_login(client)
    resp = await client.get("/api/v1/models", headers=auth_headers(token))
    assert resp.status_code == 200
    body = resp.json()
    assert [m["id"] for m in body["data"]] == ["edu-general-14b", "edu-math-7b"]
    req = gateway[0]
    assert req.method == "GET"
    assert req.url.path == "/v1/models"
    assert req.headers["X-Internal-Token"]


async def test_list_models_requires_auth(client):
    resp = await client.get("/api/v1/models")
    assert resp.status_code == 401
