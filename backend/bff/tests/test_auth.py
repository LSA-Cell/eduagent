"""鉴权流程测试：注册 / 登录 / 刷新 / 资料 / 身份切换。"""

from __future__ import annotations

from tests.conftest import auth_headers, register_and_login


async def test_register_success(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"phone": "13800000001", "password": "password123", "grade_level": "初三"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["user"]["phone"] == "13800000001"
    assert body["user"]["grade_level"] == "初三"
    assert body["user"]["user_mode"] == "student"
    tokens = body["tokens"]
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["expires_in"] == 15 * 60
    # 密码哈希不得出现在响应中
    assert "password" not in resp.text


async def test_register_duplicate_phone_conflict(client):
    await register_and_login(client)
    resp = await client.post(
        "/api/v1/auth/register", json={"phone": "13800000001", "password": "password123"}
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "phone_taken"


async def test_register_validation_error(client):
    resp = await client.post(
        "/api/v1/auth/register", json={"phone": "abc", "password": "short"}
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "validation_error"
    assert set(body.keys()) == {"code", "message", "detail"}


async def test_login_success_and_me(client):
    await register_and_login(client)
    resp = await client.post(
        "/api/v1/auth/login", json={"phone": "13800000001", "password": "password123"}
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    me = await client.get("/api/v1/users/me", headers=auth_headers(token))
    assert me.status_code == 200
    assert me.json()["phone"] == "13800000001"


async def test_login_wrong_password(client):
    await register_and_login(client)
    resp = await client.post(
        "/api/v1/auth/login", json={"phone": "13800000001", "password": "wrong-password"}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_credentials"


async def test_me_without_token_unauthorized(client):
    resp = await client.get("/api/v1/users/me")
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


async def test_refresh_flow(client):
    register = await client.post(
        "/api/v1/auth/register", json={"phone": "13800000002", "password": "password123"}
    )
    refresh_token = register.json()["tokens"]["refresh_token"]

    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    new_access = resp.json()["access_token"]
    me = await client.get("/api/v1/users/me", headers=auth_headers(new_access))
    assert me.status_code == 200
    assert me.json()["phone"] == "13800000002"


async def test_refresh_rejects_access_token(client):
    token = await register_and_login(client)
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_refresh_token"


async def test_refresh_token_single_use_rotation(client):
    """旧 refresh token 旋转后即作废，重放必须 401。"""
    register = await client.post(
        "/api/v1/auth/register", json={"phone": "13800000003", "password": "password123"}
    )
    refresh_token = register.json()["tokens"]["refresh_token"]

    first = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert first.status_code == 200
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert replay.status_code == 401
    assert replay.json()["code"] == "invalid_refresh_token"
    # 新签发的 refresh token 可继续正常轮换
    second = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first.json()["refresh_token"]}
    )
    assert second.status_code == 200


async def test_switch_user_mode(client):
    token = await register_and_login(client)
    resp = await client.put(
        "/api/v1/users/me/mode", json={"mode": "parent"}, headers=auth_headers(token)
    )
    assert resp.status_code == 200
    assert resp.json()["user_mode"] == "parent"

    resp = await client.put(
        "/api/v1/users/me/mode", json={"mode": "alien"}, headers=auth_headers(token)
    )
    assert resp.status_code == 422
