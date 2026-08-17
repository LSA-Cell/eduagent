"""两级优先级消息队列（增强机制 §2.3）。

next（控制类：停止/确认答复/ask 答复）> later（普通追问）；同级 FIFO；
上限 20 条，超出拒绝（enqueue 返回 False，由调用方提示）。
入队/出队经 on_event 回调推 message.queued / message.dequeued SSE 事件。
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Literal, Optional

Priority = Literal["next", "later"]

MAX_QUEUE_SIZE = 20  # 规格固定上限

# 控制类消息类型（停止 / 确认答复 / ask 答复）
CONTROL_KINDS: frozenset[str] = frozenset({"stop", "confirm", "ask_answer"})


@dataclass
class IncomingMessage:
    """一条进入运行中 Loop 的消息。"""

    content: str = ""
    kind: str = "text"  # text（普通追问）/ stop（停止）/ confirm / ask_answer
    priority: Priority = "later"
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)


OnQueueEvent = Callable[[str, dict[str, Any]], None]


class MessageQueue:
    """单 run 消息队列：next 优先于 later，同级 FIFO。"""

    def __init__(
        self, *, max_size: int = MAX_QUEUE_SIZE, on_event: Optional[OnQueueEvent] = None
    ) -> None:
        self._max_size = max_size
        self._next: Deque[IncomingMessage] = deque()
        self._later: Deque[IncomingMessage] = deque()
        self._on_event = on_event

    def __len__(self) -> int:
        return len(self._next) + len(self._later)

    def _emit(self, event: str, msg: IncomingMessage) -> None:
        if self._on_event is not None:
            self._on_event(event, {"message_id": msg.message_id, "priority": msg.priority})

    def enqueue(self, msg: IncomingMessage) -> bool:
        """入队（next 类控制消息不受普通排队阻塞，但仍计入总量上限）。"""
        if len(self) >= self._max_size:
            return False
        if msg.priority == "next":
            self._next.append(msg)
        else:
            self._later.append(msg)
        self._emit("message.queued", msg)
        return True

    def pop(self) -> Optional[IncomingMessage]:
        """出队：next 队列优先，同级 FIFO。"""
        msg: Optional[IncomingMessage] = None
        if self._next:
            msg = self._next.popleft()
        elif self._later:
            msg = self._later.popleft()
        if msg is not None:
            self._emit("message.dequeued", msg)
        return msg

    def peek_next(self) -> Optional[IncomingMessage]:
        """只看 next 类控制消息（立即处理路径用，不动 later 队列）。"""
        return self._next[0] if self._next else None

    def pop_next(self) -> Optional[IncomingMessage]:
        """取出一条 next 类控制消息。"""
        if not self._next:
            return None
        msg = self._next.popleft()
        self._emit("message.dequeued", msg)
        return msg

    def drain_later(self) -> list[IncomingMessage]:
        """取出全部 later 消息（turn 收尾后 steer 消费）。"""
        out: list[IncomingMessage] = []
        while self._later:
            msg = self._later.popleft()
            self._emit("message.dequeued", msg)
            out.append(msg)
        return out

    def has_pending(self) -> bool:
        return bool(self._next or self._later)
