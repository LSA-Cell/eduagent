"""Redis 滑动窗口限流器与用户分层解析。"""

from __future__ import annotations

import time
import uuid

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from eduagent_shared.logging import get_logger

log = get_logger("bff.ratelimit")


class SlidingWindowLimiter:
    """基于 Redis ZSET 的滑动窗口限流（窗口 60s）。

    Redis 不可用时 fail-open（放行并告警），避免缓存故障拖垮业务。
    """

    def __init__(self, redis_client: aioredis.Redis, *, window_seconds: int = 60, prefix: str = "rl") -> None:
        self.redis = redis_client
        self.window_seconds = window_seconds
        self.prefix = prefix

    async def allow(self, identity: str, limit: int) -> bool:
        """记录一次请求并判断是否超限。超限返回 False。"""
        now_ms = int(time.time() * 1000)
        window_ms = self.window_seconds * 1000
        key = f"{self.prefix}:{identity}"
        member = f"{now_ms}:{uuid.uuid4().hex[:12]}"
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(key, 0, now_ms - window_ms)
                pipe.zadd(key, {member: now_ms})
                pipe.zcard(key)
                pipe.expire(key, self.window_seconds)
                _, _, count, _ = await pipe.execute()
            if count > limit:
                await self.redis.zrem(key, member)  # 回滚本次计数
                return False
            return True
        except RedisError as exc:
            log.warning("rate_limit_redis_error", error=str(exc))
            return True


class RateTierResolver:
    """按用户解析限流分层：教师 > 付费 > 免费，结果缓存 60s。"""

    def __init__(self, redis_client: aioredis.Redis, session_factory=None) -> None:
        self.redis = redis_client
        self.session_factory = session_factory  # async_sessionmaker，可为 None（测试）

    async def __call__(self, user_id: str) -> str:
        cache_key = f"rltier:{user_id}"
        try:
            cached = await self.redis.get(cache_key)
        except RedisError:
            cached = None
        if cached:
            return cached

        tier = "free"
        if self.session_factory is not None:
            try:
                from eduagent_shared.models import User  # 延迟导入避免循环

                async with self.session_factory() as session:
                    user = await session.get(User, uuid.UUID(user_id))
                if user is not None:
                    if user.is_teacher:
                        tier = "teacher"
                    elif user.is_paid:
                        tier = "paid"
            except Exception as exc:  # DB 异常时按免费档处理，不阻塞请求
                log.warning("rate_tier_resolve_error", error=str(exc))

        try:
            await self.redis.set(cache_key, tier, ex=60)
        except RedisError:
            pass
        return tier
