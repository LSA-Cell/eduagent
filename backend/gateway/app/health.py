"""实例健康注册表：Redis 驱动的负载均衡与熔断。

- 健康实例列表：未被打标的实例即健康；
- 熔断：连续失败达到阈值 → SET gw:unhealthy:<instance>（TTL=MODEL_HEALTH_TTL_SECONDS），
  TTL 到期自动恢复即"半开试探"；
- 轮询：gw:rr:<model> 自增计数取模，在健康实例间轮转；
- Redis 不可用时降级为本地计数轮询 + 全部视为健康（不阻塞推理主流程）。
"""

from __future__ import annotations

from collections import defaultdict

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from eduagent_shared.logging import get_logger

log = get_logger("gateway.health")

FAILURE_THRESHOLD = 3  # 连续失败多少次后熔断


class HealthRegistry:
    def __init__(
        self,
        redis_client: aioredis.Redis,
        *,
        ttl_seconds: int = 30,
        failure_threshold: int = FAILURE_THRESHOLD,
        prefix: str = "gw",
    ) -> None:
        self.redis = redis_client
        self.ttl_seconds = ttl_seconds
        self.failure_threshold = failure_threshold
        self.prefix = prefix
        self._local_rr: dict[str, int] = defaultdict(int)  # Redis 故障时的本地轮询计数

    def _unhealthy_key(self, instance: str) -> str:
        return f"{self.prefix}:unhealthy:{instance}"

    def _fail_key(self, instance: str) -> str:
        return f"{self.prefix}:fail:{instance}"

    async def is_healthy(self, instance: str) -> bool:
        try:
            return not await self.redis.exists(self._unhealthy_key(instance))
        except RedisError as exc:
            log.warning("health_check_redis_error", error=str(exc))
            return True  # fail-open

    async def candidates(self, model: str, instances: list[str]) -> list[str]:
        """返回健康实例（按轮询偏移轮转顺序）；全部熔断时返回空列表。"""
        healthy = [i for i in instances if await self.is_healthy(i)]
        if not healthy:
            return []
        rr_key = f"{self.prefix}:rr:{model}"
        try:
            n = await self.redis.incr(rr_key)
            await self.redis.expire(rr_key, 300)
        except RedisError:
            self._local_rr[model] += 1
            n = self._local_rr[model]
        offset = (n - 1) % len(healthy)
        return healthy[offset:] + healthy[:offset]

    async def mark_success(self, instance: str) -> None:
        """成功后清除失败计数与熔断标记。"""
        try:
            await self.redis.delete(self._fail_key(instance), self._unhealthy_key(instance))
        except RedisError as exc:
            log.warning("health_mark_redis_error", error=str(exc))

    async def mark_failure(self, instance: str) -> None:
        """记录失败；连续失败达阈值则熔断（TTL 到期自动半开）。"""
        fail_key = self._fail_key(instance)
        try:
            count = await self.redis.incr(fail_key)
            await self.redis.expire(fail_key, self.ttl_seconds * 4)
            if count >= self.failure_threshold:
                await self.redis.set(
                    self._unhealthy_key(instance), "1", ex=self.ttl_seconds
                )
                await self.redis.delete(fail_key)
                log.warning(
                    "instance_marked_unhealthy",
                    instance=instance,
                    ttl_seconds=self.ttl_seconds,
                )
        except RedisError as exc:
            log.warning("health_mark_redis_error", error=str(exc))
