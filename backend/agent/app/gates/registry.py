"""feature_gates 注册表（§5.2）：表读取 + 进程内缓存 60s + 哈希分桶判定。

判定规则（is_enabled，同步纯缓存、无 I/O）：
1. 缓存过期 / 键不存在 / DB 故障 → 默认 off（红线：新机制默认 gate=off）；
2. allowlist 命中（user_id）→ on（白名单优先于分桶）；
3. enabled 且 bucket(key, user_id) < rollout_pct → on。

分桶确定性：sha256("{key}:{user_id}") 取模 100——同一 user 同一 key 结果
恒定，不随进程/时间漂移。缓存由 refresh() 异步重载（60s 热生效）。
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Callable, Optional

from sqlalchemy import select

from app.gates.models import FeatureGate

logger = logging.getLogger(__name__)

CACHE_TTL_S = 60.0


def bucket_of(key: str, user_id: str) -> int:
    """user_id 哈希分桶（0-99，纯函数，确定性）。"""
    digest = hashlib.sha256(f"{key}:{user_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


class GateRegistry:
    """feature_gates 表读取 + 进程内缓存（fail-closed：不可用一律 off）。"""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        cache_ttl_s: float = CACHE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._cache_ttl_s = cache_ttl_s
        self._clock = clock
        self._cache: dict[str, dict[str, Any]] = {}
        self._loaded_at: Optional[float] = None

    @property
    def stale(self) -> bool:
        if self._loaded_at is None:
            return True
        return (self._clock() - self._loaded_at) > self._cache_ttl_s

    async def refresh(self) -> None:
        """从 DB 重载缓存；异常保留旧缓存数据，但过期状态下 is_enabled 仍
        fail-closed 返回 off（红线：DB 不可用默认 off）。"""
        try:
            async with self._session_factory() as session:
                rows = (await session.execute(select(FeatureGate))).scalars().all()
            cache: dict[str, dict[str, Any]] = {}
            for row in rows:
                cache[str(row.key)] = {
                    "enabled": bool(getattr(row, "enabled", False)),
                    "rollout_pct": max(0, min(100, int(getattr(row, "rollout_pct", 0) or 0))),
                    "allowlist": {
                        str(u) for u in (getattr(row, "allowlist", None) or [])
                    },
                }
            self._cache = cache
            self._loaded_at = self._clock()
        except Exception:  # noqa: BLE001 - DB 故障：保留旧缓存
            logger.warning("feature_gates 加载失败，沿用旧缓存", exc_info=True)

    def is_enabled(self, key: str, user_id: Optional[str] = None) -> bool:
        """同步判定（纯缓存，无 I/O）；任何不确定情况一律 off。"""
        if self.stale:
            return False  # 缓存未就绪/过期 → 默认 off（调用方在异步路径 refresh）
        row = self._cache.get(str(key))
        if row is None:
            return False  # 键不存在 → 默认 off
        if user_id is not None and str(user_id) in row["allowlist"]:
            return True  # 白名单优先于分桶
        if not row["enabled"]:
            return False
        if row["rollout_pct"] <= 0:
            return False
        if user_id is None:
            return row["rollout_pct"] >= 100  # 无用户维度：仅全量时放行
        return bucket_of(str(key), str(user_id)) < row["rollout_pct"]


__all__ = ["CACHE_TTL_S", "GateRegistry", "bucket_of"]
