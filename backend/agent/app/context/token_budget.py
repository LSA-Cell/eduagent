"""token 粗估与窗口检查。

无 tiktoken 依赖：按 字符数 / 4 近似（中英文混合场景的保守粗估），
每条消息另计少量结构开销。只做阈值判断用，不用于计费。
"""

from __future__ import annotations

import json
from typing import Any, Sequence

CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD = 4  # 每条消息的角色/结构开销（粗估）


def estimate_text_tokens(text: str) -> int:
    """粗估一段文本的 token 数（len/4，下限 1）。"""
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def _message_text(message: dict[str, Any]) -> str:
    parts = [str(message.get("content") or "")]
    tool_calls = message.get("tool_calls")
    if tool_calls:
        parts.append(json.dumps(tool_calls, ensure_ascii=False, default=str))
    return "\n".join(parts)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    return estimate_text_tokens(_message_text(message)) + MESSAGE_OVERHEAD


def estimate_messages_tokens(messages: Sequence[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


def needs_autocompact(
    messages: Sequence[dict[str, Any]], *, window_tokens: int, buffer_tokens: int
) -> bool:
    """估算 token 是否达到（模型窗口 − buffer）触发线。"""
    return estimate_messages_tokens(messages) >= max(0, window_tokens - buffer_tokens)
