"""业务转发路由测试：mock 下游（Agent 编排/推理网关），断言方法与路径透传。

依赖覆盖 get_http_client → httpx.MockTransport，无需真实下游服务。
BFF 对外路径统一带 /api/v1 前缀（见 app.main），下游 Agent 路径无前缀。
"""

from __future__ import annotations

import json

import httpx
import pytest

from app import deps
from tests.conftest import auth_headers, register_and_login


@pytest.fixture
def downstream(app):
    """记录下游请求并返回可预期 JSON 的 mock 传输层。"""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={"ok": True, "path": request.url.path, "method": request.method},
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.dependency_overrides[deps.get_http_client] = lambda: mock_client
    return calls


async def test_get_mistakes_forwarded(client, downstream):
    token = await register_and_login(client)
    resp = await client.get(
        "/api/v1/mistakes?subject=数学", headers=auth_headers(token)
    )
    assert resp.status_code == 200
    assert resp.json()["path"] == "/mistakes"
    req = downstream[0]
    assert req.method == "GET"
    assert req.url.params["subject"] == "数学"
    # 服务间令牌与用户 ID 透传
    assert req.headers["X-Internal-Token"]
    assert req.headers["X-User-Id"]


async def test_flashcards_write_methods_forwarded(client, downstream):
    token = await register_and_login(client)
    headers = auth_headers(token)

    resp = await client.post(
        "/api/v1/flashcards", json={"front": "Q", "back": "A"}, headers=headers
    )
    assert resp.status_code == 200
    assert downstream[-1].method == "POST"
    assert downstream[-1].url.path == "/flashcards"
    assert json.loads(downstream[-1].content) == {"front": "Q", "back": "A"}

    resp = await client.post(
        "/api/v1/flashcards/generate", json={"content": "牛顿定律", "count": 5}, headers=headers
    )
    assert resp.status_code == 200
    assert downstream[-1].url.path == "/flashcards/generate"

    card_id = "00000000-0000-0000-0000-000000000001"
    resp = await client.post(
        f"/api/v1/flashcards/{card_id}/review", json={"rating": "good"}, headers=headers
    )
    assert resp.status_code == 200
    assert downstream[-1].url.path == f"/flashcards/{card_id}/review"

    resp = await client.patch(
        f"/api/v1/flashcards/{card_id}", json={"front": "Q2"}, headers=headers
    )
    assert resp.status_code == 200
    assert downstream[-1].method == "PATCH"

    resp = await client.delete(f"/api/v1/flashcards/{card_id}", headers=headers)
    assert resp.status_code == 200
    assert downstream[-1].method == "DELETE"


async def test_study_plan_create_and_checkin_forwarded(client, downstream):
    token = await register_and_login(client)
    headers = auth_headers(token)

    resp = await client.post(
        "/api/v1/study-plans",
        json={"goal_description": "期末数学上 90 分", "current_level": "初二", "days": 14},
        headers=headers,
    )
    assert resp.status_code == 200
    assert downstream[-1].url.path == "/study-plans"

    plan_id = "00000000-0000-0000-0000-000000000002"
    resp = await client.post(
        f"/api/v1/study-plans/{plan_id}/checkin", json={"day": 1}, headers=headers
    )
    assert resp.status_code == 200
    assert downstream[-1].url.path == f"/study-plans/{plan_id}/checkin"
    assert json.loads(downstream[-1].content) == {"day": 1}


async def test_mistake_review_and_variations_forwarded(client, downstream):
    token = await register_and_login(client)
    headers = auth_headers(token)
    mistake_id = "00000000-0000-0000-0000-000000000003"

    resp = await client.post(
        f"/api/v1/mistakes/{mistake_id}/review", json={"correct": True}, headers=headers
    )
    assert resp.status_code == 200
    assert downstream[-1].url.path == f"/mistakes/{mistake_id}/review"

    resp = await client.post(f"/api/v1/mistakes/{mistake_id}/variations", headers=headers)
    assert resp.status_code == 200
    assert downstream[-1].url.path == f"/mistakes/{mistake_id}/variations"


async def test_analytics_and_voice_forwarded(client, downstream):
    token = await register_and_login(client)
    headers = auth_headers(token)

    resp = await client.get("/api/v1/analytics/knowledge-mastery", headers=headers)
    assert resp.status_code == 200
    assert downstream[-1].url.path == "/analytics/knowledge-mastery"

    resp = await client.post(
        "/api/v1/voice/evaluate",
        json={"scenario": "ielts_p1", "transcript": "hello"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert downstream[-1].url.path == "/voice/evaluate"


async def test_teacher_routes_require_teacher(client, downstream, user_store):
    token = await register_and_login(client)
    headers = auth_headers(token)

    # 非教师：403
    resp = await client.get("/api/v1/teacher/lesson-plans", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"

    # 置教师身份后放行并转发
    record = await user_store.get_by_phone("13800000001")
    record.is_teacher = True

    resp = await client.post(
        "/api/v1/teacher/presentations",
        json={"topic": "牛顿第二定律", "grade": "高一", "subject": "物理"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert downstream[-1].method == "POST"
    assert downstream[-1].url.path == "/teacher/presentations"

    resp = await client.get("/api/v1/analytics/knowledge-mastery", headers=headers)
    assert resp.status_code == 200


async def test_business_routes_require_auth(client):
    resp = await client.get("/api/v1/mistakes")
    assert resp.status_code == 401


@pytest.mark.parametrize("path,body", [
    ("/uploads/presign", {"file_name": "image.jpg", "content_type": "image/jpeg", "size_bytes": 100, "purpose": "chat_image"}),
    ("/uploads/confirm", {"object_key": "u/user/image.jpg", "purpose": "chat_image"}),
])
async def test_media_post_routes_forward_auth_and_payload(client, downstream, path, body):
    token = await register_and_login(client)
    response = await client.post(f"/api/v1{path}", json=body, headers=auth_headers(token))
    assert response.status_code == 200
    request = downstream[-1]
    assert request.method == "POST"
    assert request.url.path == path
    assert json.loads(request.content) == body
    assert request.headers["X-Internal-Token"]
    assert request.headers["X-User-Id"]


async def test_media_download_route_forwards_nested_object_key(client, downstream, user_store):
    token = await register_and_login(client)
    user = await user_store.get_by_phone("13800000001")
    path = f"/files/u/{user.id}/{'a' * 32}.jpg/url"
    response = await client.get(f"/api/v1{path}", headers=auth_headers(token))
    assert response.status_code == 200
    assert downstream[-1].url.path == path
    assert downstream[-1].headers["X-User-Id"]


@pytest.mark.parametrize("key", [
    "%2e%2e/%2e%2e/v1/skills/toolbar%23",
    "%2e%2e/%2e%2e/v1/skills/toolbar%3Frole=teacher%23",
    "u/other-user/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
    "u/user/invalid.jpg",
])
async def test_media_download_rejects_unsafe_keys_before_forwarding(client, downstream, key):
    token = await register_and_login(client)
    response = await client.get(f"/api/v1/files/{key}/url", headers=auth_headers(token))
    assert response.status_code == 404
    assert downstream == []


@pytest.mark.parametrize("method,path", [
    ("POST", "/uploads/presign"),
    ("POST", "/uploads/confirm"),
    ("GET", "/files/u/user/image.jpg/url"),
])
async def test_media_routes_require_auth(client, downstream, method, path):
    response = await client.request(method, f"/api/v1{path}", json={})
    assert response.status_code == 401
    assert downstream == []
