"""追问推荐生成器（§4.1）：run 正常结束后生成 3 条候选追问。

流程：基于当前会话消息 + 当前学科 → deps.gateway 小模型（≤800ms
wait_for，超时/失败静默降级为不推荐）→ 逐条过内容审核（任一不过
滤除该条）→ suggestion_logs 落库（fail-soft）。

红线：输入只含当前会话（generate 只接收当前 run 的 messages，不存在
跨会话数据来源）；推荐未过审不下发；speculation 默认关闭。
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any, Awaitable, Callable, Optional, Sequence

from app.suggest.models import SuggestionLog

logger = logging.getLogger(__name__)

SUGGESTION_TIMEOUT_S = 0.8  # 生成 ≤800ms（非功能指标 §八.6）
SUGGESTION_COUNT = 3
MAX_SUGGESTION_CHARS = 30  # 对齐 AsyncAPI Suggestions.items.maxLength
_CONTEXT_MESSAGE_CHARS = 200  # 单条消息进 prompt 的截断长度
_CONTEXT_MESSAGES = 10  # 只取最近 N 条当前会话消息

SPECULATION_GATE_KEY = "suggestion_speculation"  # Wave D2 feature_gates 键（预留）

_SYSTEM = (
    "你是学习场景的追问推荐器。基于当前会话内容，预测用户接下来最可能想问的 "
    "3 个问题，每行一条，每条不超过 30 字，以用户第一人称口吻。只输出 3 行问题，"
    "不要编号之外的任何文字。"
)


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return None


def _render_context(messages: Sequence[dict[str, Any]], subject: Optional[str]) -> str:
    """渲染生成输入（仅当前会话消息；system 主体不含，避免提示词泄漏）。"""
    lines: list[str] = []
    if subject:
        lines.append(f"当前学科：{subject}")
    for message in list(messages)[-_CONTEXT_MESSAGES:]:
        role = message.get("role")
        if role not in ("user", "assistant", "tool"):
            continue
        content = str(message.get("content") or "")[:_CONTEXT_MESSAGE_CHARS]
        if content:
            lines.append(f"[{role}] {content}")
    return "\n".join(lines)


def _parse_items(text: str) -> list[str]:
    """解析小模型输出为候选列表（去编号/引号，≤3 条，每条 ≤30 字）。"""
    items: list[str] = []
    for line in str(text or "").splitlines():
        line = re.sub(r"^\s*(?:\d+[.、)]|[-*•])\s*", "", line).strip().strip('"“”')
        if line:
            items.append(line[:MAX_SUGGESTION_CHARS])
        if len(items) >= SUGGESTION_COUNT:
            break
    return items


class SuggestionGenerator:
    """追问推荐生成器（注入 gateway / moderate / session_factory）。"""

    def __init__(
        self,
        gateway: Any,
        *,
        moderate: Optional[Callable[[str], Awaitable[bool]]] = None,
        session_factory: Optional[Callable[[], Any]] = None,
        timeout_s: float = SUGGESTION_TIMEOUT_S,
        model: Optional[str] = None,
    ) -> None:
        self._gateway = gateway
        self._moderate = moderate
        self._session_factory = session_factory
        self._timeout_s = timeout_s
        self._model = model

    async def generate(
        self,
        *,
        messages: Sequence[dict[str, Any]],
        subject: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> list[str]:
        """生成过审后的候选追问；任何失败静默降级为 []（不影响主响应）。"""
        try:
            result = await asyncio.wait_for(
                self._gateway.chat(
                    [
                        {"role": "system", "content": _SYSTEM},
                        {"role": "user", "content": _render_context(messages, subject)},
                    ],
                    model=self._model,
                    max_tokens=200,
                ),
                timeout=self._timeout_s,
            )
            candidates = _parse_items(getattr(result, "content", "") or "")
        except Exception:  # noqa: BLE001 - 超时/网关异常静默降级
            logger.info("追问推荐生成失败，降级为不推荐", exc_info=True)
            return []
        if not candidates:
            return []
        passed = [item for item in candidates if await self._moderate_one(item)]
        if not passed:
            return []
        await self._log(user_id=user_id, session_id=session_id, items=passed)
        return passed

    async def _moderate_one(self, text: str) -> bool:
        """逐条过内容审核（复用护栏链第①环入口）；审核异常 fail-closed 滤除。"""
        if self._moderate is None:
            return True
        try:
            return bool(await self._moderate(text))
        except Exception:  # noqa: BLE001 - 审核故障一律不下发该条
            logger.warning("追问推荐审核失败，滤除该条", exc_info=True)
            return False

    async def _log(
        self,
        *,
        user_id: Optional[str],
        session_id: Optional[str],
        items: list[str],
    ) -> None:
        """suggestion_logs 落库（fail-soft；clicked_index 置 NULL）。"""
        if self._session_factory is None:
            return
        try:
            async with self._session_factory() as session:
                session.add(
                    SuggestionLog(
                        session_id=_as_uuid(session_id),
                        user_id=_as_uuid(user_id),
                        items=list(items),
                        clicked_index=None,
                    )
                )
                await session.flush()
                await session.commit()
        except Exception:  # noqa: BLE001 - 日志失败不影响推荐下发
            logger.warning("suggestion_logs 写入失败", exc_info=True)


# ---------------------------------------------------------------------------
# speculation 预取（§4.1.2）：默认关闭，Wave D2 feature_gates 落地前只留钩子
# ---------------------------------------------------------------------------
def speculation_enabled(deps: Any, user_id: Optional[str] = None) -> bool:
    """speculation Feature Gate 检查钩子：默认 False（红线：默认关闭）。

    deps.feature_gate(key, user_id=...) 由 Wave D2 的 gates 模块提供；
    缺失或异常一律按关闭处理。
    """
    gate = getattr(deps, "feature_gate", None)
    if gate is None:
        return False
    try:
        return bool(gate(SPECULATION_GATE_KEY, user_id=user_id))
    except Exception:  # noqa: BLE001 - gate 判定失败按关闭
        logger.warning("speculation gate 判定失败，按关闭处理", exc_info=True)
        return False


async def maybe_prefetch(
    deps: Any,
    *,
    suggestions: Sequence[str],
    user_id: Optional[str] = None,
    subject: Optional[str] = None,
) -> Optional[list[Any]]:
    """speculation 预取（仅只读数据：相关题目/知识点条目）。

    默认 gate=off 直接返回 None；开启后也只允许 deps.rag_search 这类
    只读检索，禁止预取任何生成类/计费类任务（红线 §4.1）。
    """
    gate_refresh = getattr(deps, "feature_gate_refresh", None)  # Wave D2：60s 热生效
    if gate_refresh is not None:
        try:
            await gate_refresh()
        except Exception:  # noqa: BLE001 - 刷新失败按 gate=off 处理
            logger.info("feature_gates 刷新失败，speculation 按关闭处理", exc_info=True)
            return None
    if not speculation_enabled(deps, user_id):
        return None
    prefetch: list[Any] = []
    for question in list(suggestions)[:SUGGESTION_COUNT]:
        try:
            prefetch.append(
                await deps.rag_search(query=question, subject=subject, top_k=3)
            )
        except Exception:  # noqa: BLE001 - 预取失败静默跳过
            logger.info("speculation 预取失败，跳过该条", exc_info=True)
    return prefetch


__all__ = [
    "SPECULATION_GATE_KEY",
    "SUGGESTION_TIMEOUT_S",
    "SuggestionGenerator",
    "maybe_prefetch",
    "speculation_enabled",
]
