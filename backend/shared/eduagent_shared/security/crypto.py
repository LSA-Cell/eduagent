"""AES-256-GCM 加解密（用户 API Key 等敏感数据落库前加密）。

存储格式：``base64url(nonce(12B) || ciphertext || tag(16B))``，nonce 随密文一起存储，
解密时拆分即可，无需额外存储。

密钥轮换说明（rotate）：
1. 生成新的 32 字节随机密钥，base64 后写入 ``KEY_ENCRYPTION_MASTER_KEY``；
2. 旧密钥临时配置到 ``KEY_ENCRYPTION_MASTER_KEY_PREVIOUS``，解密时新钥失败自动回退旧钥；
3. 跑一次性脚本：读出所有 ``user_api_keys.encrypted_key``，decrypt 后用新钥 encrypt 重写；
4. 全量重写完成后移除 PREVIOUS 配置。建议轮换周期 90 天（见 .env.example）。
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import get_settings

_NONCE_SIZE = 12  # GCM 推荐 96 位 nonce


class DecryptError(ValueError):
    """解密失败（密文损坏或密钥不匹配）。"""


def _load_key(raw: str) -> bytes:
    """把配置字符串解析成 32 字节密钥。

    标准格式为 32 字节的 base64；开发环境常见的占位字符串（非合法 base64 或长度不符）
    统一用 SHA-256 派生，保证本地可跑——生产环境必须配置标准 base64 密钥。
    """
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except Exception:
        key = b""
    if len(key) != 32:
        key = hashlib.sha256(raw.encode("utf-8")).digest()
    return key


def _master_key() -> bytes:
    return _load_key(get_settings().key_encryption_master_key)


def encrypt_key(plaintext: str, *, master_key: bytes | None = None) -> str:
    """加密明文，返回 base64url 字符串（nonce 已内嵌）。"""
    key = master_key or _master_key()
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_key(token: str, *, master_key: bytes | None = None) -> str:
    """解密 encrypt_key 的输出；轮换期自动回退旧主密钥。"""
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    nonce, ciphertext = raw[:_NONCE_SIZE], raw[_NONCE_SIZE:]
    key = master_key or _master_key()
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None).decode("utf-8")
    except InvalidTag:
        previous = get_settings().key_encryption_master_key_previous
        if previous:
            try:
                return AESGCM(_load_key(previous)).decrypt(nonce, ciphertext, None).decode("utf-8")
            except InvalidTag:
                pass
        raise DecryptError("密文解密失败：密钥不匹配或数据损坏") from None


def mask_api_key(plaintext: str) -> str:
    """返回掩码形式（仅保留后 4 位），用于接口展示，绝不回显完整明文。"""
    tail = plaintext[-4:] if len(plaintext) >= 4 else plaintext
    return f"****{tail}"
