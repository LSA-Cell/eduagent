"""压缩保护清单与压缩审计工具（四级管线共享）。

保护清单（压缩禁区）：当前 turn 消息、最近一次工具结果全文、
``<learner_memory>`` 记忆区块、标记"收藏/重要"的消息。
"""

from __future__ import annotations

from typing import Any, Sequence

from app.context.token_budget import estimate_messages_tokens

MEMORY_BLOCK_TAG = "<learner_memory>"


def is_memory_block(message: dict[str, Any]) -> bool:
    """注入了记忆区块的 system 消息（禁止压缩改写）。"""
    return MEMORY_BLOCK_TAG in str(message.get("content") or "")


def is_marked_important(message: dict[str, Any]) -> bool:
    """用户明确标记"收藏/重要"的消息（禁止压缩）。"""
    if message.get("pinned") or message.get("important"):
        return True
    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        return bool(metadata.get("pinned") or metadata.get("important") or metadata.get("favorite"))
    return False


def is_tool_result(message: dict[str, Any]) -> bool:
    return message.get("role") == "tool"


def last_tool_result_index(messages: Sequence[dict[str, Any]]) -> int:
    """最近一次工具结果全文所在下标（无则 -1）。"""
    for i in range(len(messages) - 1, -1, -1):
        if is_tool_result(messages[i]):
            return i
    return -1


def is_protected(message: dict[str, Any], index: int, *, current_turn_start: int, last_tool_index: int) -> bool:
    """保护清单判定：system 基座 / 当前 turn / 最近工具结果 / 记忆区块 / 收藏重要。"""
    if message.get("role") == "system":
        return True
    if index >= current_turn_start:
        return True
    if index == last_tool_index:
        return True
    return is_memory_block(message) or is_marked_important(message)


def make_audit(
    level: str,
    before: Sequence[dict[str, Any]],
    after: Sequence[dict[str, Any]],
    summary: str,
) -> dict[str, Any]:
    """压缩审计记录（对应 context_compactions 表）。"""
    return {
        "level": level,
        "tokens_before": estimate_messages_tokens(before),
        "tokens_after": estimate_messages_tokens(after),
        "summary": summary,
    }
