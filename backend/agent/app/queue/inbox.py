"""运行中消息收件箱（deps 注入，Loop 决策点非阻塞 poll）。

生产形态：多请求并发时，打断/追问消息经 POST /v1/chat/messages 或
POST /v1/chat/stop 写入收件箱（按 session_id 归键）；运行中的 Loop 在
流式 delta、工具执行轮询点 poll 取回全部待处理消息。
InMemoryInbox 用于单进程；多副本部署换 RedisInbox（list：RPUSH 入 / LPOP 清空）。
两者同为异步 poll 接口；测试直接注入预载消息的 InMemoryInbox。
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, DefaultDict

from app.queue.message_queue import IncomingMessage

logger = logging.getLogger(__name__)


class InMemoryInbox:
    """单进程收件箱：key（session_id 或 run_id）→ 消息列表。"""

    def __init__(self) -> None:
        self._boxes: DefaultDict[str, list[IncomingMessage]] = defaultdict(list)

    async def push(self, key: str, msg: IncomingMessage) -> None:
        self._boxes[key].append(msg)

    async def poll(self, key: str) -> list[IncomingMessage]:
        """取走该 key 下全部消息（无则空列表；无 I/O 等待）。"""
        box = self._boxes.pop(key, None)
        return list(box) if box else []

    def pending_count(self, key: str) -> int:
        return len(self._boxes.get(key, ()))


class RedisInbox:
    """多副本收件箱：Redis list（键 inbox:{key}，TTL 5 分钟兜底）。"""

    def __init__(self, redis: Any, *, ttl_s: int = 300) -> None:
        self._redis = redis
        self._ttl_s = ttl_s

    async def push(self, key: str, msg: IncomingMessage) -> None:
        redis_key = f"inbox:{key}"
        await self._redis.rpush(
            redis_key,
            json.dumps(
                {
                    "message_id": msg.message_id,
                    "kind": msg.kind,
                    "priority": msg.priority,
                    "content": msg.content,
                },
                ensure_ascii=False,
            ),
        )
        await self._redis.expire(redis_key, self._ttl_s)

    async def poll(self, key: str) -> list[IncomingMessage]:
        out: list[IncomingMessage] = []
        while True:
            raw = await self._redis.lpop(f"inbox:{key}")
            if raw is None:
                break
            try:
                data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
                out.append(
                    IncomingMessage(
                        content=str(data.get("content") or ""),
                        kind=str(data.get("kind") or "text"),
                        priority=data.get("priority") or "later",
                        message_id=str(data.get("message_id") or ""),
                    )
                )
            except Exception:  # noqa: BLE001 - 坏消息跳过
                logger.warning("inbox 消息解析失败", exc_info=True)
        return out
