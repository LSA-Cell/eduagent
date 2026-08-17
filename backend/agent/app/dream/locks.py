"""DreamTask 分布式锁（§4.2.3）：用户粒度，锁冲突即跳过当日整合。

RedisDreamLock 兼容 redis.asyncio 与同步 redis-py（鸭子类型，结果可 await 则 await）；
InMemoryDreamLock 为离线测试 fake。红线：未持锁禁止执行整合——锁语义只在本模块
与 pipeline 的持锁闸口中出现，业务代码不绕路。
"""

from __future__ import annotations

import inspect
import uuid
from datetime import date
from typing import Any, Optional

LOCK_TTL_S = 3600  # 单用户整合 ≤60s（非功能指标 §八.5），1 小时兜底防死锁


def lock_key(user_id: str, day: date) -> str:
    """锁键：用户 + 活动日期粒度。"""
    return f"dream:lock:{user_id}:{day.isoformat()}"


class RedisDreamLock:
    """Redis SET NX EX 分布式锁（用户粒度）。"""

    def __init__(self, redis: Any, ttl_s: int = LOCK_TTL_S) -> None:
        self._redis = redis
        self._ttl_s = ttl_s
        self._tokens: dict[str, str] = {}  # key → token（release 时比对，防误删他人锁）

    async def _call(self, result: Any) -> Any:
        if inspect.isawaitable(result):
            return await result
        return result

    async def acquire(self, user_id: str, day: date) -> bool:
        """尝试持锁；成功返回 True（冲突即 False，调用方跳过该用户当日整合）。"""
        key = lock_key(user_id, day)
        token = uuid.uuid4().hex
        ok = await self._call(self._redis.set(key, token, nx=True, ex=self._ttl_s))
        if ok:
            self._tokens[key] = token
        return bool(ok)

    async def is_held(self, user_id: str, day: date) -> bool:
        """当前进程是否持有该锁（consolidate 前的防御性复核用）。"""
        key = lock_key(user_id, day)
        token = self._tokens.get(key)
        if token is None:
            return False
        current = await self._call(self._redis.get(key))
        return current == token

    async def release(self, user_id: str, day: date) -> None:
        """释放锁（仅当 token 匹配，避免误删续约后的他人锁）。"""
        key = lock_key(user_id, day)
        token = self._tokens.pop(key, None)
        if token is None:
            return
        current = await self._call(self._redis.get(key))
        if current == token:
            await self._call(self._redis.delete(key))


class InMemoryDreamLock:
    """进程内锁 fake：离线测试用（语义同 RedisDreamLock）。"""

    def __init__(self) -> None:
        self._held: dict[str, str] = {}  # key → token

    async def acquire(self, user_id: str, day: date) -> bool:
        key = lock_key(user_id, day)
        if key in self._held:
            return False
        self._held[key] = uuid.uuid4().hex
        return True

    async def is_held(self, user_id: str, day: date) -> bool:
        return lock_key(user_id, day) in self._held

    async def release(self, user_id: str, day: date) -> None:
        self._held.pop(lock_key(user_id, day), None)


__all__ = ["LOCK_TTL_S", "InMemoryDreamLock", "RedisDreamLock", "lock_key"]
