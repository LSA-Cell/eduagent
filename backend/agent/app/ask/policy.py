"""ask 超时分级策略与答复注入文案（增强机制 §2.2.5）。

- 普通澄清超时 → 按推荐项（首位选项）默认值继续，并在注入文本中明示
  （模型后续回答必须向用户说明已采用默认值）；
- 敏感决策（付费/删除/公开发布/家长确认，sensitive=true）超时 → 一律按取消，
  禁止"推荐项默认继续"（红线 2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class AskOutcome:
    """一次挂起的收尾结果。"""

    cancelled: bool = False  # 敏感决策超时按取消
    content: str = ""  # 回注入消息历史的工具结果文本


def format_answers(questions: list[dict[str, Any]], answers: dict[str, Any]) -> str:
    """用户答复 → 回注文本（question → 选中 labels）。"""
    lines = ["用户已答复提问："]
    for q in questions:
        question = str(q.get("question", ""))
        selected = answers.get(question) or []
        if isinstance(selected, str):
            selected = [selected]
        labels = [str(s) for s in selected] or ["（未选择）"]
        lines.append(f"- {question}：{'、'.join(labels)}")
    return "\n".join(lines)


def default_continue_content(questions: list[dict[str, Any]]) -> str:
    """普通澄清超时：按推荐项默认继续（明示，供模型向用户说明）。"""
    lines = [
        "用户超时未答复，已按推荐项默认值继续（请在后续回答中向用户明示已采用默认选择）："
    ]
    for q in questions:
        question = str(q.get("question", ""))
        options = q.get("options") or []
        first_label = str(options[0].get("label", "")) if options else "（无选项）"
        lines.append(f"- {question}：{first_label}（默认）")
    return "\n".join(lines)


def resolve_timeout(questions: list[dict[str, Any]]) -> AskOutcome:
    """超时收尾：任一题敏感 → 取消；否则推荐项默认继续。"""
    if any(bool(q.get("sensitive")) for q in questions):
        return AskOutcome(
            cancelled=True,
            content="敏感决策超时未答复，本次操作已按取消处理（未执行任何变更）。",
        )
    return AskOutcome(content=default_continue_content(questions))
