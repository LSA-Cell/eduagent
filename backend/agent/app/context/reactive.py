"""L4 reactive compact：收到 prompt_too_long 后的响应式压缩。

比 L3 更激进：仅保留保护消息 + 最近 2 条原文，其余生成摘要。
每 turn 仅 1 次机会（由 TurnState.reactive_compact_used 控制）。
失败同样抛 CompactionError，禁止裸丢消息。
"""

from __future__ import annotations

from typing import Any, Sequence

from app.context.autocompact import BOUNDARY_MARKER, CompactionError, _summarize
from app.context.protect import is_protected, last_tool_result_index, make_audit

REACTIVE_KEEP_RECENT = 2


async def reactive_compact(
    messages: Sequence[dict[str, Any]],
    *,
    gateway: Any,
    model: str | None = None,
    current_turn_start: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """响应式压缩，返回 (新消息列表, 审计 dict)。失败抛 CompactionError。"""
    last_tool = last_tool_result_index(messages)
    # 压缩边界：最近 REACTIVE_KEEP_RECENT 条与当前 turn 的并集保留原文
    boundary = min(current_turn_start, max(0, len(messages) - REACTIVE_KEEP_RECENT))
    head: list[dict[str, Any]] = []
    middle: list[dict[str, Any]] = []
    for i, message in enumerate(messages[:boundary]):
        if is_protected(message, i, current_turn_start=current_turn_start, last_tool_index=last_tool):
            head.append(message)
        else:
            middle.append(message)
    if not middle:
        # 已无压缩空间：prompt_too_long 重试也无意义，抛错让上层终止
        raise CompactionError("reactive compact 无可压缩内容")
    summary = await _summarize(middle, gateway=gateway, model=model)
    boundary_message = {
        "role": "system",
        "content": f"[响应式压缩边界] 以下是之前对话的摘要：\n{summary}",
        BOUNDARY_MARKER: True,
    }
    result = head + [boundary_message] + list(messages[boundary:])
    return result, make_audit("reactive", messages, result, summary)
