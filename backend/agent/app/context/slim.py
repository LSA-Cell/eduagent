"""L1 工具结果瘦身：旧工具结果全文替换为摘要占位。

每轮循环开头执行。执行器已在写入消息历史时对超限结果做过一次
"存 MinIO + 摘要替换"；本级处理的是随轮次老化的历史工具结果：
超过 max_chars 的旧 tool 消息替换为一行占位（按 tool_call_id 定位，
保护清单内的消息不触碰）。纯函数。
"""

from __future__ import annotations

from typing import Any, Sequence

from app.context.protect import (
    is_protected,
    is_tool_result,
    last_tool_result_index,
    make_audit,
)

DEFAULT_SLIM_MAX_CHARS = 2000


def slim_tool_results(
    messages: Sequence[dict[str, Any]],
    *,
    current_turn_start: int,
    max_chars: int = DEFAULT_SLIM_MAX_CHARS,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """返回 (新消息列表, 审计 dict 或 None)。未触发时原样返回。"""
    last_tool = last_tool_result_index(messages)
    result: list[dict[str, Any]] = []
    slimmed = 0
    for i, message in enumerate(messages):
        if (
            is_tool_result(message)
            and len(str(message.get("content") or "")) > max_chars
            and not is_protected(
                message, i, current_turn_start=current_turn_start, last_tool_index=last_tool
            )
        ):
            name = message.get("name") or "tool"
            original = len(str(message.get("content") or ""))
            replaced = dict(message)
            replaced["content"] = (
                f"[历史工具结果已瘦身：{name} 的结果原 {original} 字符，"
                f"tool_call_id={message.get('tool_call_id', '')}]"
            )
            replaced["slimmed"] = True
            result.append(replaced)
            slimmed += 1
        else:
            result.append(message)
    if not slimmed:
        return list(messages), None
    return result, make_audit("slim", messages, result, f"L1 瘦身 {slimmed} 条历史工具结果")
