"""L2 microcompact：只保留最近 K 个工具结果全文。

每轮循环开头执行；更早的工具结果替换为一行摘要占位。
保护清单（当前 turn / 记忆区块 / 收藏重要）不触碰。纯函数。
"""

from __future__ import annotations

from typing import Any, Sequence

from app.context.protect import (
    is_protected,
    is_tool_result,
    last_tool_result_index,
    make_audit,
)

DEFAULT_KEEP_FULL = 3


def microcompact(
    messages: Sequence[dict[str, Any]],
    *,
    current_turn_start: int,
    keep_full: int = DEFAULT_KEEP_FULL,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """返回 (新消息列表, 审计 dict 或 None)。未触发时原样返回。"""
    last_tool = last_tool_result_index(messages)
    # 收集未被保护的 tool 结果下标（按时间正序）
    tool_indices = [
        i
        for i, m in enumerate(messages)
        if is_tool_result(m)
        and not is_protected(m, i, current_turn_start=current_turn_start, last_tool_index=last_tool)
    ]
    if len(tool_indices) <= keep_full:
        return list(messages), None
    to_compact = set(tool_indices[: len(tool_indices) - keep_full])
    result: list[dict[str, Any]] = []
    for i, message in enumerate(messages):
        if i in to_compact:
            name = message.get("name") or "tool"
            replaced = dict(message)
            replaced["content"] = f"[更早的工具结果已压缩：{name}]"
            replaced["microcompacted"] = True
            result.append(replaced)
        else:
            result.append(message)
    return result, make_audit(
        "microcompact", messages, result,
        f"L2 压缩 {len(to_compact)} 条工具结果（保留最近 {keep_full} 条全文）",
    )
