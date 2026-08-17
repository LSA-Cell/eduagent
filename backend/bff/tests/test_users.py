"""用户资料更新与教师认证提交测试。"""

from __future__ import annotations

from tests.conftest import auth_headers, register_and_login


async def test_update_profile(client):
    token = await register_and_login(client)
    headers = auth_headers(token)

    resp = await client.put(
        "/api/v1/users/me",
        json={"grade_level": "高一", "default_subject": "数学"},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["grade_level"] == "高一"
    assert body["default_subject"] == "数学"

    # 仅提交部分字段时，其余保持原值
    resp = await client.put("/api/v1/users/me", json={"grade_level": "高二"}, headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["grade_level"] == "高二"
    assert body["default_subject"] == "数学"


async def test_update_profile_requires_auth(client):
    resp = await client.put("/api/v1/users/me", json={"grade_level": "高一"})
    assert resp.status_code == 401


async def test_teacher_certification_accepted(client):
    token = await register_and_login(client)
    resp = await client.post(
        "/api/v1/users/me/teacher-certification",
        json={"file_url": "http://minio/eduagent-uploads/cert/a.pdf", "note": "教师资格证"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "pending_review"


async def test_teacher_certification_validation(client):
    token = await register_and_login(client)
    resp = await client.post(
        "/api/v1/users/me/teacher-certification", json={}, headers=auth_headers(token)
    )
    assert resp.status_code == 422
