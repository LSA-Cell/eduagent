"""PromptSuggestion 追问推荐（增强机制 §4.1）。

红线：推荐必须逐条过内容审核（复用 deps.moderate，即护栏链第①环
同一入口）后才下发；生成只用当前会话 + 当前学科，禁止跨会话挖掘；
speculation 预取默认关闭（gate 钩子默认 False，Wave D2 gates 落地）。
"""

from app.suggest.generator import (
    SPECULATION_GATE_KEY,
    SuggestionGenerator,
    maybe_prefetch,
    speculation_enabled,
)
from app.suggest.models import SuggestionLog

__all__ = [
    "SPECULATION_GATE_KEY",
    "SuggestionGenerator",
    "SuggestionLog",
    "maybe_prefetch",
    "speculation_enabled",
]
