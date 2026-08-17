"""Prompt-Cache 感知压缩（增强机制 §3.1）：前缀稳定区校验与缓存破坏检测。

前缀稳定区 = System Prompt 主体（含 ``<learner_memory>`` 记忆区块与
``<output_style>`` 风格区块，均位于 messages[0] 的 system 内容内）+
常驻工具定义（resident schemas）。动态挂载的工具（§4.3）追加在常驻
工具之后，属于"压缩边界之后"的后缀，不参与前缀 hash。

约束落实：
- L1–L4 压缩只允许在压缩边界之后编辑：``guard_prefix`` 对比压缩前后
  前缀区，任何改写抛 ``PrefixViolationError``；Loop 侧的
  ``checked_apply`` 检测后拒绝应用（保留原消息）并落审计 + 告警；
- 每次模型请求前 ``CacheBreakWatcher.observe`` 计算前缀 hash 与上一次
  对比，破坏即写审计（复用 context_compactions，level="cache_break"）
  并触发 ``on_cache_break`` 告警回调（红线：仅日志视为未实现）；
- 记忆注入输出确定性由 ``app.memory.inject.format_memory_block`` 的
  固定排序/截断规则保证（禁止时间戳等易变字段进前缀）。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Sequence

from app.context.token_budget import estimate_messages_tokens

logger = logging.getLogger(__name__)

CACHE_BREAK_AUDIT_LEVEL = "cache_break"  # 复用 context_compactions 表（level 列）

# 审计回调：async (record: dict) -> None（实现方 fail-soft）
CacheAudit = Callable[[dict[str, Any]], Awaitable[None]]
# 告警回调：(info: dict) -> None 或 Awaitable[None]；DefaultDeps 接日志+指标占位
CacheBreakAlert = Callable[[dict[str, Any]], Any]


class PrefixViolationError(RuntimeError):
    """压缩/注入逻辑改写了前缀稳定区（红线 §3.1.1）。"""


def _canon(value: Any) -> str:
    """确定性序列化（字节级稳定比较用）。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def prefix_envelope(
    messages: Sequence[dict[str, Any]],
    tool_schemas: Optional[Sequence[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """提取前缀稳定区：messages[0] 的 system 内容 + 工具定义。

    system 主体 / 记忆区块 / output_style 区块都在 messages[0] 内；
    工具定义经 tools 参数进网关，此处以常驻 schemas 快照参与 hash。
    """
    system = ""
    if messages and messages[0].get("role") == "system":
        system = str(messages[0].get("content") or "")
    return {"system": system, "tools": list(tool_schemas or [])}


def prefix_hash(
    messages: Sequence[dict[str, Any]],
    tool_schemas: Optional[Sequence[dict[str, Any]]] = None,
) -> str:
    """前缀稳定区的 sha256（每请求前计算并与上一次对比）。"""
    return hashlib.sha256(_canon(prefix_envelope(messages, tool_schemas)).encode("utf-8")).hexdigest()


def guard_prefix(
    before: Sequence[dict[str, Any]],
    after: Sequence[dict[str, Any]],
    *,
    level: str = "",
    tools_before: Optional[Sequence[dict[str, Any]]] = None,
    tools_after: Optional[Sequence[dict[str, Any]]] = None,
) -> None:
    """校验压缩/注入未改写前缀稳定区；改写即抛 PrefixViolationError。"""
    if prefix_envelope(before, tools_before) != prefix_envelope(after, tools_after):
        raise PrefixViolationError(
            f"{level or '压缩/注入'} 改写了前缀稳定区"
            "（system 主体/工具定义/记忆区块/output_style），违反缓存红线"
        )


async def _notify(callback: Optional[CacheBreakAlert], info: dict[str, Any]) -> None:
    if callback is None:
        return
    try:
        result = callback(info)
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001 - 告警回调异常不阻断主流程
        logger.warning("on_cache_break 告警回调失败", exc_info=True)


async def checked_apply(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    *,
    level: str,
    audit: Optional[CacheAudit] = None,
    on_cache_break: Optional[CacheBreakAlert] = None,
    session_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """压缩结果应用闸口：前缀被改写时拒绝应用（返回原消息）+ 审计 + 告警。

    返回值为可安全使用的消息列表（与 before/after 之一同一对象）。
    """
    try:
        guard_prefix(before, after, level=level)
    except PrefixViolationError as exc:
        record = {
            "level": CACHE_BREAK_AUDIT_LEVEL,
            "tokens_before": estimate_messages_tokens(before),
            "tokens_after": estimate_messages_tokens(after),
            "summary": f"[{level}] {exc}",
        }
        if audit is not None:
            try:
                await audit(record)
            except Exception:  # noqa: BLE001 - 审计失败仅记日志（fail-soft）
                logger.warning("缓存破坏审计写入失败", exc_info=True)
        await _notify(
            on_cache_break,
            {"source": level, "session_id": session_id, "detail": str(exc)},
        )
        return before  # 拒绝应用，保留原消息
    return after


@dataclass
class CacheBreakWatcher:
    """逐请求前缀 hash 对比器（每 run 一个实例，随 Loop 销毁）。

    首次 observe 建立基线（此时记忆注入已完成）；此后每次模型请求前
    调用 observe，hash 变化即落审计（context_compactions,
    level="cache_break"）并触发 on_cache_break 告警。
    """

    audit: Optional[CacheAudit] = None
    on_cache_break: Optional[CacheBreakAlert] = None
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    last_hash: Optional[str] = None

    async def observe(
        self,
        messages: Sequence[dict[str, Any]],
        tool_schemas: Optional[Sequence[dict[str, Any]]] = None,
        *,
        source: str,
    ) -> bool:
        """对比前缀 hash；破坏返回 True 并落审计+告警，否则 False。"""
        current = prefix_hash(messages, tool_schemas)
        previous = self.last_hash
        self.last_hash = current
        if previous is None or previous == current:
            return False
        info = {
            "source": source,  # 定位变更来源（哪次请求/哪级压缩之后）
            "session_id": self.session_id,
            "run_id": self.run_id,
            "previous_hash": previous,
            "current_hash": current,
        }
        if self.audit is not None:
            try:
                await self.audit({
                    "level": CACHE_BREAK_AUDIT_LEVEL,
                    "tokens_before": estimate_messages_tokens(messages),
                    "tokens_after": estimate_messages_tokens(messages),
                    "summary": (
                        f"前缀 hash 破坏（{source}）："
                        f"{previous[:12]}… → {current[:12]}…"
                    ),
                })
            except Exception:  # noqa: BLE001 - 审计失败仅记日志（fail-soft）
                logger.warning("缓存破坏审计写入失败", exc_info=True)
        await _notify(self.on_cache_break, info)
        return True


__all__ = [
    "CACHE_BREAK_AUDIT_LEVEL",
    "CacheBreakWatcher",
    "PrefixViolationError",
    "checked_apply",
    "guard_prefix",
    "prefix_envelope",
    "prefix_hash",
]
