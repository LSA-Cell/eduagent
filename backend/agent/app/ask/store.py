"""ask 挂起状态存储（增强机制 §2.2：挂起状态存 Redis，TTL 120s）。

接口（Loop 与 API 共用，测试注入 fake）：
    register(...) -> ask_id                     # Loop 侧：登记挂起
    wait(ask_id, timeout_s) -> dict | None      # Loop 侧：挂起等答复，超时返回 None
    resolve(ask_id, answers, user_id=...) -> bool   # API 侧：写入答复并唤醒等待方

InMemoryAskStore：单进程实现（asyncio.Future 唤醒）；多副本部署换 RedisAskStore
（ask:{id} 挂起记录 + ask:{id}:answers 答复键，TTL 120s，轮询唤醒）。
两种实现都保证：超时/断线不泄漏——Loop 侧只等用户答复或超时事件，期间无任何
模型调用（红线：禁止模型空转计费）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

ASK_TTL_S = 120.0  # 挂起 TTL（规格固定值）


@dataclass
class PendingAsk:
    """一次挂起的结构化提问。"""

    ask_id: str
    user_id: str
    run_id: Optional[str]
    questions: list[dict[str, Any]]
    expires_at: float = 0.0
    answers: Optional[dict[str, Any]] = None  # {question 文本: [选中 label, ...]}
    future: Optional[asyncio.Future] = field(default=None, repr=False)


class InMemoryAskStore:
    """单进程挂起存储（asyncio.Future 唤醒；过期懒清理）。"""

    def __init__(self, *, ttl_s: float = ASK_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._items: dict[str, PendingAsk] = {}

    def _purge(self) -> None:
        now = time.monotonic()
        for key in [k for k, v in self._items.items() if v.expires_at <= now]:
            self._items.pop(key, None)

    async def register(
        self,
        *,
        user_id: str,
        run_id: Optional[str],
        questions: list[dict[str, Any]],
        ttl_s: Optional[float] = None,
    ) -> str:
        self._purge()
        ask_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        self._items[ask_id] = PendingAsk(
            ask_id=ask_id,
            user_id=str(user_id),
            run_id=run_id,
            questions=questions,
            expires_at=time.monotonic() + float(ttl_s or self._ttl_s),
            future=loop.create_future(),
        )
        return ask_id

    async def wait(self, ask_id: str, *, timeout_s: float) -> Optional[dict[str, Any]]:
        """挂起等待答复；超时返回 None（期间无模型调用）。"""
        item = self._items.get(ask_id)
        if item is None or item.future is None:
            return None
        try:
            return await asyncio.wait_for(asyncio.shield(item.future), timeout=float(timeout_s))
        except asyncio.TimeoutError:
            self._items.pop(ask_id, None)
            return None

    async def resolve(
        self, ask_id: str, answers: dict[str, Any], *, user_id: Optional[str] = None
    ) -> bool:
        """写入答复并唤醒挂起的 Loop；不存在/过期/跨用户返回 False。"""
        self._purge()
        item = self._items.get(ask_id)
        if item is None:
            return False
        if user_id is not None and item.user_id != str(user_id):
            return False
        item.answers = dict(answers)
        future = item.future
        self._items.pop(ask_id, None)
        if future is not None and not future.done():
            future.set_result(item.answers)
        return True

    def get(self, ask_id: str) -> Optional[PendingAsk]:
        self._purge()
        return self._items.get(ask_id)


class RedisAskStore:
    """多副本部署形态：挂起记录与答复都放 Redis（TTL 120s），轮询唤醒。

    redis 参数为 redis.asyncio 语义的异步客户端（生产 DefaultDeps 注入）。
    """

    def __init__(self, redis: Any, *, ttl_s: float = ASK_TTL_S, poll_s: float = 0.2) -> None:
        self._redis = redis
        self._ttl_s = ttl_s
        self._poll_s = poll_s

    async def register(
        self,
        *,
        user_id: str,
        run_id: Optional[str],
        questions: list[dict[str, Any]],
        ttl_s: Optional[float] = None,
    ) -> str:
        ask_id = uuid.uuid4().hex
        payload = json.dumps(
            {"user_id": str(user_id), "run_id": run_id, "questions": questions},
            ensure_ascii=False,
        )
        await self._redis.set(f"ask:{ask_id}", payload, ex=int(ttl_s or self._ttl_s))
        return ask_id

    async def wait(self, ask_id: str, *, timeout_s: float) -> Optional[dict[str, Any]]:
        deadline = time.monotonic() + float(timeout_s)
        while time.monotonic() < deadline:
            raw = await self._redis.get(f"ask:{ask_id}:answers")
            if raw:
                try:
                    await self._redis.delete(f"ask:{ask_id}:answers")
                except Exception:  # noqa: BLE001
                    pass
                return json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
            await asyncio.sleep(self._poll_s)
        return None

    async def resolve(
        self, ask_id: str, answers: dict[str, Any], *, user_id: Optional[str] = None
    ) -> bool:
        try:
            raw = await self._redis.get(f"ask:{ask_id}")
            if raw is None:
                return False
            record = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
            if user_id is not None and record.get("user_id") != str(user_id):
                return False
            ttl = await self._redis.ttl(f"ask:{ask_id}")
            await self._redis.set(
                f"ask:{ask_id}:answers",
                json.dumps(answers, ensure_ascii=False),
                ex=max(1, int(ttl) if ttl and ttl > 0 else int(self._ttl_s)),
            )
            return True
        except Exception:  # noqa: BLE001 - Redis 故障按未找到处理
            logger.warning("ask resolve 写 Redis 失败", exc_info=True)
            return False
