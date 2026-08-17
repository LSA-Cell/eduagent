"""L3 autocompact：达窗口触发线时调推理网关生成整段摘要。

触发条件由 token_budget.needs_autocompact 判定（窗口 − 13k buffer）。
压缩点之后保留原文，写入压缩边界标记；保护清单内消息不参与摘要、原样保留。

红线：摘要生成失败禁止裸丢消息 —— 抛 CompactionError，由上层走
响应式压缩或终止本 turn。
"""

from __future__ import annotations

from typing import Any, Sequence

from app.context.protect import (
    is_protected,
    last_tool_result_index,
    make_audit,
)

SUMMARY_SYSTEM_PROMPT = (
    "你是上下文压缩器。把给定的多轮对话历史压缩为一段保留关键信息的摘要"
    "（用户目标、已完成的工具操作与结论、重要事实），控制在 500 字以内，"
    "只输出摘要文本。"
)

BOUNDARY_MARKER = "compaction_boundary"  # 压缩边界标记键


class CompactionError(RuntimeError):
    """压缩失败（摘要生成失败等）；上层决定走响应式压缩或终止。"""


def _render_for_summary(messages: Sequence[dict[str, Any]]) -> str:
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "?")
        content = str(m.get("content") or "")
        if len(content) > 500:
            content = content[:500] + "…"
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


async def _summarize(messages: Sequence[dict[str, Any]], *, gateway: Any, model: str | None) -> str:
    try:
        result = await gateway.chat(
            [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": _render_for_summary(messages)},
            ],
            model=model,
            tools=None,
        )
        summary = (result.content or "").strip()
    except Exception as exc:  # noqa: BLE001 - 统一包装，禁止裸丢消息
        raise CompactionError(f"autocompact 摘要生成失败: {exc}") from exc
    if not summary:
        raise CompactionError("autocompact 摘要为空")
    return summary


async def autocompact(
    messages: Sequence[dict[str, Any]],
    *,
    gateway: Any,
    model: str | None = None,
    current_turn_start: int,
    keep_recent: int = 6,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """生成整段摘要压缩旧历史，返回 (新消息列表, 审计 dict)。

    失败抛 CompactionError（调用方保证不裸丢消息）。
    """
    last_tool = last_tool_result_index(messages)
    # 压缩边界：最近 keep_recent 条与当前 turn 的并集保留原文
    boundary = min(current_turn_start, max(0, len(messages) - keep_recent))
    head: list[dict[str, Any]] = []  # 保护消息（不压缩）
    middle: list[dict[str, Any]] = []  # 参与摘要
    for i, message in enumerate(messages[:boundary]):
        if is_protected(message, i, current_turn_start=current_turn_start, last_tool_index=last_tool):
            head.append(message)
        else:
            middle.append(message)
    if not middle:
        # 无可压缩内容：不算失败，返回零收益审计
        return list(messages), make_audit("autocompact", messages, messages, "无可压缩内容")
    summary = await _summarize(middle, gateway=gateway, model=model)
    boundary_message = {
        "role": "system",
        "content": f"[压缩边界] 以下是之前对话的摘要：\n{summary}",
        BOUNDARY_MARKER: True,
    }
    result = head + [boundary_message] + list(messages[boundary:])
    return result, make_audit("autocompact", messages, result, summary)
