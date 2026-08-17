"""记忆注入格式化：``<learner_memory>`` 区块，≤800 token 超限按优先级截断。

优先级：preference（显式偏好/目标）> weakness（薄弱点）> review（到期复习）
> profile（画像）> general。返回区块文本与 memory_ids 列表（审计用）。
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from app.context.token_budget import estimate_text_tokens

MEMORY_BLOCK_MAX_TOKENS = 800
MEMORY_BLOCK_OPEN = "<learner_memory>"
MEMORY_BLOCK_CLOSE = "</learner_memory>"

_TYPE_PRIORITY = {
    "preference": 0,
    "weakness": 1,
    "review": 2,
    "profile": 3,
    "general": 4,
}

_TYPE_LABEL = {
    "preference": "学习偏好/目标",
    "weakness": "薄弱知识点",
    "review": "待复习",
    "profile": "学习画像",
    "general": "其它",
}


def _get(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _sort_key(item: Any) -> tuple[int, float]:
    mtype = str(_get(item, "memory_type", "general") or "general")
    importance = float(_get(item, "importance", 0.5) or 0.5)
    return (_TYPE_PRIORITY.get(mtype, 4), -importance)


def format_memory_block(
    items: Sequence[Any], *, max_tokens: int = MEMORY_BLOCK_MAX_TOKENS
) -> tuple[str, list[str]]:
    """把预取结果格式化为记忆区块，返回 (区块文本, memory_ids)。

    空结果返回 ("", [])；超限按优先级截断（先丢低优先级）。
    """
    ordered = sorted(items, key=_sort_key)
    lines: list[str] = []
    memory_ids: list[str] = []
    used_tokens = estimate_text_tokens(MEMORY_BLOCK_OPEN) + estimate_text_tokens(MEMORY_BLOCK_CLOSE)
    truncated = False
    for item in ordered:
        content = str(_get(item, "content", "") or "").strip()
        if not content:
            continue
        mtype = str(_get(item, "memory_type", "general") or "general")
        line = f"- [{_TYPE_LABEL.get(mtype, '其它')}] {content}"
        line_tokens = estimate_text_tokens(line)
        if used_tokens + line_tokens > max_tokens:
            truncated = True
            break
        lines.append(line)
        used_tokens += line_tokens
        memory_id: Optional[Any] = _get(item, "id")
        if memory_id:
            memory_ids.append(str(memory_id))
    if not lines:
        return "", []
    if truncated:
        lines.append("- …（更多记忆已按优先级截断）")
    block = "\n".join([MEMORY_BLOCK_OPEN, *lines, MEMORY_BLOCK_CLOSE])
    return block, memory_ids
