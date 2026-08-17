"""三条恢复路径的辅助状态机（设计文档 §3.1 恢复路径表）。

1. 模型返回 max_output_tokens：自动续写，最多连续 MAX_CONTINUATIONS 次；
2. 网关返回 prompt_too_long：响应式压缩后重试，全 turn 仅 1 次机会；
3. 同类失败连续 CIRCUIT_BREAKER_THRESHOLD 次熔断（禁止静默重试烧 Token）。
"""

from __future__ import annotations

from app.loop.turn_state import TurnState

MAX_CONTINUATIONS = 3
CIRCUIT_BREAKER_THRESHOLD = 3

# finish_reason 中视为"输出被截断"的取值（OpenAI 风格 length + 网关方言）
TRUNCATED_FINISH_REASONS = frozenset({"length", "max_output_tokens"})

CONTINUATION_PROMPT = "（你的上次回复被长度截断，请从截断处继续，不要重复已输出的内容。）"


def is_truncated(finish_reason: str) -> bool:
    """模型输出是否因 max_output_tokens 被截断（可续写）。"""
    return (finish_reason or "") in TRUNCATED_FINISH_REASONS


def can_continue(continuations: int) -> bool:
    """续写次数未达上限。"""
    return continuations < MAX_CONTINUATIONS


def can_reactive_retry(state: TurnState) -> bool:
    """prompt_too_long 响应式压缩重试机会（每 turn 仅 1 次）。"""
    return not state.reactive_compact_used


def record_failure(state: TurnState, kind: str) -> bool:
    """记录同类失败；返回 True 表示已达熔断阈值。"""
    return state.record_failure(kind) >= CIRCUIT_BREAKER_THRESHOLD


def reset_failures(state: TurnState) -> None:
    """任意环节成功后清零失败计数。"""
    state.reset_failures()
