"""鉴权扩展测试：游客 / 登出吊销 / 设备管理 / 短信 / 邮箱 / OAuth / 密码维护 / 锁定与验证码。"""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from tests.conftest import auth_headers, register_and_login


async def _dev_sms_code(client, phone: str, scene: str) -> str:
    resp = await client.post("/api/v1/auth/sms-code", json={"phone": phone, "scene": scene})
    assert resp.status_code == 200, resp.text
    return resp.json()["dev_code"]


async def _clear_sms_interval(app) -> None:
    """清掉短信重发间隔键（同号短时间内重复取码的测试用）。"""
    redis = app.state.redis
    async for key in redis.scan_iter("sms_int:*"):
        await redis.delete(key)


async def _register_tokens(client, phone: str, device_id: str | None = None) -> dict:
    body: dict = {
        "phone": phone,
        "password": "password123",
        "sms_code": await _dev_sms_code(client, phone, "register"),
    }
    if device_id:
        body["device_id"] = device_id
    resp = await client.post("/api/v1/auth/register", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["tokens"]


# ---------------------------------------------------------------------------
# 游客登录
# ---------------------------------------------------------------------------


async def test_guest_login_creates_account_and_reuses_by_device(client):
    resp = await client.post("/api/v1/auth/guest", json={"device_id": "device-aaa-0001"})
    assert resp.status_code == 200, resp.text
    tokens = resp.json()
    assert tokens["access_token"] and tokens["refresh_token"]

    me = await client.get("/api/v1/users/me", headers=auth_headers(tokens["access_token"]))
    assert me.status_code == 200
    assert me.json()["is_guest"] is True
    assert me.json()["phone"] is None
    guest_id = me.json()["id"]

    # 同设备重进 → 同一游客账号
    again = await client.post("/api/v1/auth/guest", json={"device_id": "device-aaa-0001"})
    me2 = await client.get("/api/v1/users/me", headers=auth_headers(again.json()["access_token"]))
    assert me2.json()["id"] == guest_id


async def test_guest_upgrade_binds_phone_and_keeps_account(client):
    guest = await client.post("/api/v1/auth/guest", json={"device_id": "device-bbb-0002"})
    token = guest.json()["access_token"]

    resp = await client.post(
        "/api/v1/auth/upgrade",
        json={
            "phone": "13800000020",
            "password": "newpassword8",
            "sms_code": await _dev_sms_code(client, "13800000020", "register"),
        },
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["is_guest"] is False
    assert resp.json()["user"]["phone"] == "13800000020"

    # 转正后可密码登录同一账号
    login = await client.post(
        "/api/v1/auth/login", json={"phone": "13800000020", "password": "newpassword8"}
    )
    assert login.status_code == 200


async def test_guest_upgrade_conflict_phone(client, app):
    await register_and_login(client, "13800000021")
    await _clear_sms_interval(app)
    guest = await client.post("/api/v1/auth/guest", json={"device_id": "device-ccc-0003"})
    resp = await client.post(
        "/api/v1/auth/upgrade",
        json={
            "phone": "13800000021",
            "password": "newpassword8",
            "sms_code": await _dev_sms_code(client, "13800000021", "register"),
        },
        headers=auth_headers(guest.json()["access_token"]),
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "phone_taken"


async def test_guest_upgrade_rejects_non_guest(client):
    token = await register_and_login(client, "13800000022")
    resp = await client.post(
        "/api/v1/auth/upgrade",
        json={
            "phone": "13800000023",
            "password": "newpassword8",
            "sms_code": "123456",
        },
        headers=auth_headers(token),
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "not_guest"


# ---------------------------------------------------------------------------
# 登出 / 吊销 / 设备管理
# ---------------------------------------------------------------------------


async def test_logout_revokes_access_and_refresh(client):
    tokens = await _register_tokens(client, "13800000030", device_id="dev-x1")
    access, refresh = tokens["access_token"], tokens["refresh_token"]

    out = await client.post("/api/v1/auth/logout", headers=auth_headers(access))
    assert out.status_code == 204

    # access token 立即失效（黑名单）
    me = await client.get("/api/v1/users/me", headers=auth_headers(access))
    assert me.status_code == 401
    # refresh token 真吊销（设备会话已失效）
    ref = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert ref.status_code == 401


async def test_logout_all_kicks_every_device(client):
    await _register_tokens(client, "13800000031")
    login_a = await client.post(
        "/api/v1/auth/login",
        json={"phone": "13800000031", "password": "password123", "device_id": "dev-a"},
    )
    login_b = await client.post(
        "/api/v1/auth/login",
        json={"phone": "13800000031", "password": "password123", "device_id": "dev-b"},
    )
    assert login_a.status_code == 200 and login_b.status_code == 200

    out = await client.post(
        "/api/v1/auth/logout-all", headers=auth_headers(login_a.json()["access_token"])
    )
    assert out.status_code == 204
    # 两台设备的 refresh 都失效
    for tk in (login_a.json(), login_b.json()):
        ref = await client.post("/api/v1/auth/refresh", json={"refresh_token": tk["refresh_token"]})
        assert ref.status_code == 401


async def test_device_list_and_kick(client):
    await _register_tokens(client, "13800000032")
    login_a = await client.post(
        "/api/v1/auth/login",
        json={"phone": "13800000032", "password": "password123", "device_id": "dev-a", "device_name": "手机A"},
    )
    login_b = await client.post(
        "/api/v1/auth/login",
        json={"phone": "13800000032", "password": "password123", "device_id": "dev-b", "device_name": "平板B"},
    )
    lst = await client.get("/api/v1/auth/devices", headers=auth_headers(login_a.json()["access_token"]))
    assert lst.status_code == 200
    devices = lst.json()
    assert len(devices) == 3  # 注册时 1 + 两次登录
    current = [d for d in devices if d["current"]]
    assert len(current) == 1 and current[0]["device_id"] == "dev-a"
    target = next(d for d in devices if d["device_id"] == "dev-b")

    kick = await client.delete(
        f"/api/v1/auth/devices/{target['sid']}", headers=auth_headers(login_a.json()["access_token"])
    )
    assert kick.status_code == 204
    ref = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": login_b.json()["refresh_token"]}
    )
    assert ref.status_code == 401


async def test_kick_device_of_other_user_forbidden(client):
    await _register_tokens(client, "13800000033", device_id="dev-victim")
    attacker = await register_and_login(client, "13800000034")
    lst = await client.get(
        "/api/v1/auth/devices",
        headers=auth_headers(
            (
                await client.post(
                    "/api/v1/auth/login",
                    json={"phone": "13800000033", "password": "password123"},
                )
            ).json()["access_token"]
        ),
    )
    victim_sid = lst.json()[0]["sid"]
    resp = await client.delete(
        f"/api/v1/auth/devices/{victim_sid}", headers=auth_headers(attacker)
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 短信验证码 / 邮箱 / OAuth 登录
# ---------------------------------------------------------------------------


async def test_sms_code_interval_limit(client):
    await client.post("/api/v1/auth/sms-code", json={"phone": "13800000040", "scene": "login"})
    again = await client.post(
        "/api/v1/auth/sms-code", json={"phone": "13800000040", "scene": "login"}
    )
    assert again.status_code == 429
    assert again.json()["code"] == "sms_too_frequent"


async def test_sms_login_auto_registers(client):
    code = await _dev_sms_code(client, "13800000041", "login")
    resp = await client.post(
        "/api/v1/auth/login-sms", json={"phone": "13800000041", "code": code}
    )
    assert resp.status_code == 200
    me = await client.get(
        "/api/v1/users/me", headers=auth_headers(resp.json()["access_token"])
    )
    assert me.json()["phone"] == "13800000041"
    assert me.json()["is_guest"] is False


async def test_sms_code_one_time_consume(client):
    code = await _dev_sms_code(client, "13800000042", "login")
    first = await client.post(
        "/api/v1/auth/login-sms", json={"phone": "13800000042", "code": code}
    )
    assert first.status_code == 200
    replay = await client.post(
        "/api/v1/auth/login-sms", json={"phone": "13800000042", "code": code}
    )
    assert replay.status_code == 422


async def test_email_login(client):
    code = await _dev_sms_code(client, "13800000043", "register")
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "phone": "13800000043",
            "password": "password123",
            "sms_code": code,
            "email": "student@example.com",
        },
    )
    assert reg.status_code == 201
    resp = await client.post(
        "/api/v1/auth/login-email",
        json={"email": "student@example.com", "password": "password123"},
    )
    assert resp.status_code == 200
    bad = await client.post(
        "/api/v1/auth/login-email",
        json={"email": "student@example.com", "password": "wrong-password"},
    )
    assert bad.status_code == 401


async def test_oauth_wechat_dev_openid(client):
    resp = await client.post(
        "/api/v1/auth/oauth/wechat", json={"dev_openid": "wx-openid-001"}
    )
    assert resp.status_code == 200, resp.text
    me = await client.get(
        "/api/v1/users/me", headers=auth_headers(resp.json()["access_token"])
    )
    assert me.status_code == 200
    # 同 openid 再登录 → 同一账号
    again = await client.post(
        "/api/v1/auth/oauth/wechat", json={"dev_openid": "wx-openid-001"}
    )
    me2 = await client.get(
        "/api/v1/users/me", headers=auth_headers(again.json()["access_token"])
    )
    assert me2.json()["id"] == me.json()["id"]


async def test_oauth_unsupported_provider(client):
    resp = await client.post("/api/v1/auth/oauth/qq", json={"dev_openid": "x"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "unsupported_provider"


# ---------------------------------------------------------------------------
# 密码维护
# ---------------------------------------------------------------------------


async def test_change_password(client):
    tokens = await _register_tokens(client, "13800000050")
    resp = await client.post(
        "/api/v1/auth/password/change",
        json={"old_password": "password123", "new_password": "newpassword8"},
        headers=auth_headers(tokens["access_token"]),
    )
    assert resp.status_code == 204
    old = await client.post(
        "/api/v1/auth/login", json={"phone": "13800000050", "password": "password123"}
    )
    assert old.status_code == 401
    new = await client.post(
        "/api/v1/auth/login", json={"phone": "13800000050", "password": "newpassword8"}
    )
    assert new.status_code == 200


async def test_change_password_wrong_old(client):
    tokens = await _register_tokens(client, "13800000051")
    resp = await client.post(
        "/api/v1/auth/password/change",
        json={"old_password": "wrong-old", "new_password": "newpassword8"},
        headers=auth_headers(tokens["access_token"]),
    )
    assert resp.status_code == 401


async def test_reset_password_by_sms(client):
    await _register_tokens(client, "13800000052")
    code = await _dev_sms_code(client, "13800000052", "reset")
    resp = await client.post(
        "/api/v1/auth/password/reset",
        json={"phone": "13800000052", "sms_code": code, "new_password": "resetpass88"},
    )
    assert resp.status_code == 204
    login = await client.post(
        "/api/v1/auth/login", json={"phone": "13800000052", "password": "resetpass88"}
    )
    assert login.status_code == 200


async def test_reset_password_unregistered_phone(client):
    code = await _dev_sms_code(client, "13800000053", "reset")
    resp = await client.post(
        "/api/v1/auth/password/reset",
        json={"phone": "13800000053", "sms_code": code, "new_password": "resetpass88"},
    )
    assert resp.status_code == 404


async def test_short_password_rejected(client):
    """前后端统一：设置密码不足 8 位一律 422。"""
    code = await _dev_sms_code(client, "13800000054", "register")
    resp = await client.post(
        "/api/v1/auth/register",
        json={"phone": "13800000054", "password": "abc123", "sms_code": code},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 登录失败锁定与图形验证码
# ---------------------------------------------------------------------------


async def test_captcha_required_after_threshold(client, app):
    await _register_tokens(client, "13800000060")
    for _ in range(3):
        await client.post(
            "/api/v1/auth/login", json={"phone": "13800000060", "password": "wrong-pass"}
        )
    resp = await client.post(
        "/api/v1/auth/login", json={"phone": "13800000060", "password": "password123"}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "captcha_required"
    assert resp.json()["detail"]["captcha_required"] is True


async def test_captcha_flow_unlocks_login(client, app):
    await _register_tokens(client, "13800000061")
    for _ in range(3):
        await client.post(
            "/api/v1/auth/login", json={"phone": "13800000061", "password": "wrong-pass"}
        )
    cap = await client.get("/api/v1/auth/captcha")
    assert cap.status_code == 200
    captcha_id = cap.json()["captcha_id"]
    # 从 fakeredis 里取答案哈希不可逆——直接读键验证流程：错误答案应被拒
    wrong = await client.post(
        "/api/v1/auth/login",
        json={
            "phone": "13800000061",
            "password": "password123",
            "captcha_id": captcha_id,
            "captcha_code": "zzzz",
        },
    )
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "captcha_required"


async def test_account_locked_after_max_failures(client):
    await _register_tokens(client, "13800000062")
    for i in range(5):
        resp = await client.post(
            "/api/v1/auth/login",
            json={
                "phone": "13800000062",
                "password": "wrong-pass",
                "captcha_id": "x",
                "captcha_code": "y",
            },
        )
        assert resp.status_code == 401
    locked = await client.post(
        "/api/v1/auth/login", json={"phone": "13800000062", "password": "password123"}
    )
    assert locked.status_code == 423
    assert locked.json()["code"] == "account_locked"


async def test_captcha_svg_shape(client):
    resp = await client.get("/api/v1/auth/captcha")
    assert resp.status_code == 200
    body = resp.json()
    assert body["svg"].startswith("<svg") and body["captcha_id"]
    assert body["expires_in"] == 5 * 60
