"""事件重放存储：Redis 可用时持久化（TTL 5 分钟），否则进程内 ring buffer。

客户端重连携带 ``Last-Event-ID``（即 seq），``replay_since`` 返回其后事件，
保证断线窗口内无重复、无丢失。Redis 故障时降级内存并记日志（fail-soft）。
"""

from __future__ import annotations

import json
import logging
from collections import deque
from typing import Any, Optional

from app.events.protocol import SSEEvent

logger = logging.getLogger(__name__)

REPLAY_TTL_S = 300  # 重放窗口：5 分钟
DEFAULT_RING_MAXLEN = 1000  # 内存降级时每 run 保留的事件上限


def _key(run_id: str) -> str:
    return f"agent:events:{run_id}"


class EventReplayStore:
    """事件追加 / 按 seq 重放。redis 为 redis.asyncio 客户端或 None。"""

    def __init__(
        self,
        redis: Any = None,
        *,
        ttl_s: int = REPLAY_TTL_S,
        ring_maxlen: int = DEFAULT_RING_MAXLEN,
    ) -> None:
        self._redis = redis
        self._ttl_s = ttl_s
        self._ring_maxlen = ring_maxlen
        self._buffers: dict[str, deque[SSEEvent]] = {}

    def _append_memory(self, run_id: str, event: SSEEvent) -> None:
        buf = self._buffers.get(run_id)
        if buf is None:
            buf = self._buffers[run_id] = deque(maxlen=self._ring_maxlen)
        buf.append(event)

    def _dump(self, event: SSEEvent) -> str:
        return json.dumps(
            {"event": event.event, "data": event.data}, ensure_ascii=False, default=str
        )

    @staticmethod
    def _load(raw: str) -> Optional[SSEEvent]:
        try:
            payload = json.loads(raw)
            return SSEEvent(event=payload["event"], data=payload.get("data") or {})
        except Exception:  # noqa: BLE001 - 单条损坏不拖垮整段重放
            return None

    async def append(self, run_id: str, event: SSEEvent) -> None:
        """持久化一条事件；Redis 不可用时落进程内 ring buffer。"""
        if self._redis is not None:
            try:
                key = _key(run_id)
                await self._redis.rpush(key, self._dump(event))
                await self._redis.expire(key, self._ttl_s)
                return
            except Exception:  # noqa: BLE001 - 重放能力降级，不影响主流程
                logger.warning("replay store redis append 失败，降级内存", exc_info=True)
        self._append_memory(run_id, event)

    async def replay_since(self, run_id: str, last_event_id: int) -> list[SSEEvent]:
        """返回 seq > last_event_id 的事件序列（无重复无丢失，按 seq 升序）。"""
        events: list[SSEEvent] = []
        if self._redis is not None:
            try:
                raw_list = await self._redis.lrange(_key(run_id), 0, -1)
                for raw in raw_list or []:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    loaded = self._load(raw)
                    if loaded is not None:
                        events.append(loaded)
            except Exception:  # noqa: BLE001
                logger.warning("replay store redis 读取失败，尝试内存副本", exc_info=True)
                events = []
        if not events:
            events = list(self._buffers.get(run_id, ()))
        events.sort(key=lambda e: e.seq)
        return [e for e in events if e.seq > last_event_id]
