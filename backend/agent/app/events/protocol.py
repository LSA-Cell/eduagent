"""SSE 事件协议：事件类型枚举、事件模型与单调递增序列号。

契约见设计文档第四章：事件类型固定，禁止新增协议外字段；
每个事件携带 run_id 与单调递增 seq（在 data 内）。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """统一 SSE 事件类型枚举（协议固定，禁止扩展协议外字段）。"""

    RUN_START = "run.start"
    MESSAGE_DELTA = "message.delta"
    TOOL_START = "tool.start"
    TOOL_PROGRESS = "tool.progress"
    CARD = "card"
    GUARDRAIL_ASK = "guardrail.ask"
    MEMORY_USED = "memory.used"
    ERROR = "error"
    RUN_END = "run.end"
    # ---- 增强机制扩展包 §六（Wave C 交互协作机制；AsyncAPI 先行更新） ----
    PLAN_OUTLINE = "plan.outline"
    PLAN_STATUS = "plan.status"
    ASK = "ask"
    MESSAGE_QUEUED = "message.queued"
    MESSAGE_DEQUEUED = "message.dequeued"
    RUN_INTERRUPTED = "run.interrupted"
    TOOL_CANCELLED = "tool.cancelled"


class SSEEvent(BaseModel):
    """一条 SSE 事件：event 为类型，data 必须含单调递增 seq。"""

    event: str
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def seq(self) -> int:
        return int(self.data.get("seq", 0))


class SSESequencer:
    """seq 发号器：单次 Loop 运行内单调递增（从 1 开始）。"""

    def __init__(self, start: int = 0) -> None:
        self._seq = start

    @property
    def current(self) -> int:
        return self._seq

    def next(self) -> int:
        self._seq += 1
        return self._seq

    def make(self, event: EventType | str, **data: Any) -> SSEEvent:
        """构造事件并自动分配 seq（data 内禁止自带 seq，统一由此发放）。"""
        data.pop("seq", None)
        return SSEEvent(
            event=str(event.value if isinstance(event, EventType) else event),
            data={"seq": self.next(), **data},
        )
