"""Loop 共享纯函数：工具调用并发分批。

规则（设计文档 §3.2）：按模型输出顺序扫描，连续且同时满足
``is_read_only && is_concurrency_safe`` 的划入同一并发批（上限
``MAX_CONCURRENT_BATCH``）；遇到写工具立即切批，写工具单独串行批。
未注册/未知工具按写工具保守处理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_CONCURRENT_BATCH = 5


class BatchableTool(Protocol):
    """分批所需的工具元数据视图（EduTool 鸭子类型）。"""

    is_read_only: bool
    is_concurrency_safe: bool


@dataclass
class ToolBatch:
    """一个执行批次：concurrent=True 时 calls 可 asyncio.gather，否则串行。"""

    calls: list[Any] = field(default_factory=list)
    concurrent: bool = False


def _is_concurrent_safe(tool: Any) -> bool:
    return bool(
        tool is not None
        and getattr(tool, "is_read_only", False)
        and getattr(tool, "is_concurrency_safe", False)
    )


def partition_tool_calls(calls: list[Any], tool_lookup: Any) -> list[ToolBatch]:
    """把有序工具调用划分为执行批次。

    ``tool_lookup`` 为 ``name -> 工具或 None`` 的映射（或带 maybe_get 的注册表）。
    返回批次保持原顺序；并发批内 calls 长度 ≤ MAX_CONCURRENT_BATCH。
    """
    batches: list[ToolBatch] = []
    pending: list[Any] = []

    def lookup(name: str) -> Any:
        if hasattr(tool_lookup, "maybe_get"):
            return tool_lookup.maybe_get(name)
        return tool_lookup.get(name)

    def flush_pending() -> None:
        nonlocal pending
        while pending:
            chunk, pending = pending[:MAX_CONCURRENT_BATCH], pending[MAX_CONCURRENT_BATCH:]
            batches.append(ToolBatch(calls=chunk, concurrent=True))

    for call in calls:
        tool = lookup(getattr(call, "name", ""))
        if _is_concurrent_safe(tool):
            pending.append(call)
            continue
        flush_pending()
        batches.append(ToolBatch(calls=[call], concurrent=False))
    flush_pending()
    return batches
