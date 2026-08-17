"""API Key 管理测试：AES-256-GCM 加密往返 + 接口掩码不回显明文。"""

from __future__ import annotations

from eduagent_shared.security.crypto import decrypt_key, encrypt_key, mask_api_key
from tests.conftest import auth_headers, register_and_login

_TEST_KEY = "sk-test-abcdef1234567890"


def test_crypto_roundtrip():
    """加密 -> 解密往返一致；密文不含明文。"""
    token = encrypt_key(_TEST_KEY)
    assert _TEST_KEY not in token
    assert decrypt_key(token) == _TEST_KEY
    # 两次加密 nonce 不同，密文不同
    assert encrypt_key(_TEST_KEY) != token


def test_mask_api_key():
    assert mask_api_key(_TEST_KEY) == "****7890"
    assert mask_api_key("ab") == "****ab"


async def test_create_list_delete_api_key(client, api_key_store):
    token = await register_and_login(client)
    headers = auth_headers(token)

    # 创建
    resp = await client.post(
        "/api/v1/users/me/api-keys",
        json={"provider": "openai", "api_key": _TEST_KEY, "monthly_budget": 1000},
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["provider"] == "openai"
    assert body["masked_key"] == "****7890"
    assert _TEST_KEY not in resp.text  # 明文绝不回显
    key_id = body["id"]

    # 落库为密文（内存仓储直接断言）
    stored = list(api_key_store._records.values())[0]
    assert stored.encrypted_key != _TEST_KEY
    assert decrypt_key(stored.encrypted_key) == _TEST_KEY

    # 列表（服务端解密取掩码，仍不回显明文）
    resp = await client.get("/api/v1/users/me/api-keys", headers=headers)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["masked_key"] == "****7890"
    assert _TEST_KEY not in resp.text

    # 重复绑定同一 provider → 409
    resp = await client.post(
        "/api/v1/users/me/api-keys",
        json={"provider": "openai", "api_key": "sk-another-key-0001"},
        headers=headers,
    )
    assert resp.status_code == 409

    # 删除
    resp = await client.delete(f"/api/v1/users/me/api-keys/{key_id}", headers=headers)
    assert resp.status_code == 204
    resp = await client.get("/api/v1/users/me/api-keys", headers=headers)
    assert resp.json() == []

    # 删除不存在的 → 404
    resp = await client.delete(f"/api/v1/users/me/api-keys/{key_id}", headers=headers)
    assert resp.status_code == 404


async def test_api_key_requires_auth(client):
    resp = await client.get("/api/v1/users/me/api-keys")
    assert resp.status_code == 401
