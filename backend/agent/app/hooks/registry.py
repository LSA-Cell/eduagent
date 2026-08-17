"""automation_hooks 注册表读取（§5.4）：进程内缓存 60s，DB 故障降级为空配置。

与 feature_gates/output_styles 同一模式：refresh() 异步加载入缓存，
hooks_for() 同步纯缓存查询（判定路径无 I/O）。缓存过期视为不可用，
由异步调用方（dispatcher）先 refresh 再读。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from sqlalchemy import select

from app.hooks.models import AutomationHook

logger = logging.getLogger(__name__)

CACHE_TTL_S = 60.0
EVENTS = ("mistake.created", "plan.confirmed", "dream.completed")  # 首批事件（封闭）
ACTIONS = ("notify_parent", "notify_teacher")  # 首批动作（封闭）


class HookRegistry:
    """automation_hooks 表读取 + 进程内缓存（fail-soft：异常保留旧缓存）。"""

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
        self._cache: dict[str, list[dict[str, Any]]] = {}
        self._loaded_at: Optional[float] = None

    @property
    def stale(self) -> bool:
        """缓存是否过期（未加载或超过 TTL）。"""
        if self._loaded_at is None:
            return True
        return (self._clock() - self._loaded_at) > self._cache_ttl_s

    async def refresh(self) -> None:
        """从 DB 重载缓存；任何异常保留旧缓存（首次失败则保持空配置）。"""
        try:
            async with self._session_factory() as session:
                rows = (
                    await session.execute(
                        select(AutomationHook).where(AutomationHook.is_active.is_(True))
                    )
                ).scalars().all()
            cache: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                if not getattr(row, "is_active", True):  # Python 侧复核
                    continue
                event = str(getattr(row, "event", "") or "")
                action = str(getattr(row, "action", "") or "")
                if event not in EVENTS or action not in ACTIONS:  # 协议外配置跳过
                    continue
                owner_id = getattr(row, "owner_id", None)
                cache.setdefault(event, []).append({
                    "hook_id": str(getattr(row, "id", "")),
                    "action": action,
                    "owner_id": str(owner_id) if owner_id else None,
                    "config": dict(getattr(row, "config", None) or {}),
                })
            self._cache = cache
            self._loaded_at = self._clock()
        except Exception:  # noqa: BLE001 - DB 故障：保留旧缓存（无旧缓存=空配置）
            logger.warning("automation_hooks 加载失败，沿用旧缓存", exc_info=True)

    def hooks_for(self, event: str) -> list[dict[str, Any]]:
        """同步缓存查询（无 I/O）；未知事件返回空。"""
        return list(self._cache.get(event, []))


__all__ = ["ACTIONS", "CACHE_TTL_S", "EVENTS", "HookRegistry"]
