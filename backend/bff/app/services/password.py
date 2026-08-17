"""密码哈希（bcrypt）。

bcrypt 仅处理前 72 字节，超长密码截断处理（先 UTF-8 编码）。
"""

from __future__ import annotations

import bcrypt


def _to_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    """生成 bcrypt 哈希。"""
    return bcrypt.hashpw(_to_bytes(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """校验密码；哈希格式异常时返回 False 而不是抛错。"""
    try:
        return bcrypt.checkpw(_to_bytes(password), password_hash.encode("ascii"))
    except ValueError:
        return False
