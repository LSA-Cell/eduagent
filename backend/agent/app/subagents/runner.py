"""同步子 Agent 运行器：进程内递归 edu_agent_loop，结果以单 ToolResult 返回。

红线落实（设计文档 §3.3 / 八.9）：
- 独立 messages、独立 run_id（由子 Loop 自生成）、裁剪工具池
  （allowlist ∩ 父注册表，且永远剔除 sub_agent_dispatch —— 禁止派生孙 Agent）；
- 禁止读父会话完整历史：子请求 session_id=None，只传构造好的任务描述与参数；
- 最大深度 2：depth > 2 的派发直接结构化拒绝；
- 学科模型路由覆盖：subject_routing[subject] → default，模型名只来自注册表；
- 失败/超时一律回注结构化错误 ToolResult（可重试文案），异常禁止冒泡中断父 Loop。
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any, Optional

from app.events.protocol import EventType
from app.loop.agent_loop import edu_agent_loop
from app.subagents.loader import SubAgentDefinition
from app.tools.base import ToolContext, ToolResult
from app.tools.registry import EduToolRegistry

logger = logging.getLogger(__name__)

MAX_SUBAGENT_DEPTH = 2  # 父 Loop=1，子 Agent=2，禁止再派生


def build_child_registry(
    parent_registry: Any, definition: SubAgentDefinition
) -> EduToolRegistry:
    """裁剪工具池：只保留 allowlist 内且父注册表已注册的工具。

    sub_agent_dispatch 无条件剔除（红线：子 Agent 禁止派生孙 Agent）；
    allowlist 中未注册的名字记日志跳过（注册表之外/不存在的工具进不了子上下文）。
    """
    child = EduToolRegistry()
    for name in definition.tools_allowlist:
        if name == "sub_agent_dispatch":
            continue
        tool = parent_registry.maybe_get(name)
        if tool is None:
            logger.warning("子 Agent %s allowlist 含未注册工具 %s，已跳过", definition.name, name)
            continue
        child.register(tool)
    return child


def route_model(definition: SubAgentDefinition, subject: Optional[str]) -> Optional[str]:
    """学科 → 推理网关模型名（注册表路由；无命中取 default，再无则 None=网关默认）。"""
    routing = definition.subject_routing or {}
    if subject and subject in routing:
        return routing[subject]
    return routing.get("default")


class _ChildDeps:
    """子 Agent Deps：全部 I/O 委托父 deps，仅工具注册表替换为裁剪池。"""

    def __init__(self, parent: Any, tool_registry: EduToolRegistry) -> None:
        self._parent = parent
        self.tool_registry = tool_registry

    def __getattr__(self, name: str) -> Any:
        return getattr(self._parent, name)


def _compose_message(
    definition: SubAgentDefinition, task: str, params: dict[str, Any]
) -> str:
    """组装子 Agent 的用户消息（只含结构化参数与必要上下文，不带父历史）。"""
    parts = [f"【{definition.description}】", task.strip() or "（未提供任务描述）"]
    if params:
        parts.append("参数：" + json.dumps(params, ensure_ascii=False, default=str))
    return "\n".join(parts)


async def _consume(definition: SubAgentDefinition, req: Any, deps: Any) -> ToolResult:
    """消费子 Loop 事件流，聚合文本/卡片/错误为单个 ToolResult。"""
    text_parts: list[str] = []
    cards: list[dict[str, Any]] = []
    errors: list[str] = []
    child_run_id: Optional[str] = None
    async for event in edu_agent_loop(req, deps):
        if child_run_id is None:
            child_run_id = event.data.get("run_id")
        if event.event == EventType.MESSAGE_DELTA.value:
            text_parts.append(str(event.data.get("text", "")))
        elif event.event == EventType.CARD.value:
            cards.append({
                "card_type": event.data.get("card_type"),
                "card_data": event.data.get("card_data"),
                "file_urls": event.data.get("file_urls") or [],
            })
        elif event.event == EventType.ERROR.value:
            errors.append(str(event.data.get("message", "未知错误"))[:200])
    card_data: dict[str, Any] = {"agent": definition.name, "run_id": child_run_id}
    if cards:
        card_data["cards"] = cards
    if errors:
        card_data["status"] = "failed"
        card_data["retryable"] = True
        content = f"子 Agent {definition.name} 执行失败：{errors[-1]}（可重试或换个说法降级处理）"
        if text_parts:
            content += "\n部分输出：" + "".join(text_parts)[:500]
        return ToolResult(content=content, card_type=definition.card_type, card_data=card_data)
    content = "".join(text_parts).strip() or f"子 Agent {definition.name} 已完成（无文本输出）。"
    return ToolResult(content=content, card_type=definition.card_type, card_data=card_data)


async def run_sync_subagent(
    definition: SubAgentDefinition,
    *,
    task: str,
    params: Optional[dict[str, Any]] = None,
    subject: Optional[str],
    deps: Any,
    parent_ctx: ToolContext,
    depth: int = 2,
) -> ToolResult:
    """进程内同步执行子 Agent，返回单个 ToolResult（不抛异常）。"""
    if depth > MAX_SUBAGENT_DEPTH:
        return ToolResult(
            content=f"已达最大子 Agent 深度（{MAX_SUBAGENT_DEPTH}），禁止继续派生。",
            card_data={"agent": definition.name, "status": "depth_exceeded", "retryable": False},
        )
    child_registry = build_child_registry(deps.tool_registry, definition)
    child_deps = _ChildDeps(deps, child_registry)
    req = SimpleNamespace(
        user_id=parent_ctx.user_id,
        session_id=None,  # 红线：禁止读父会话完整历史
        subject=subject,
        model=route_model(definition, subject),
        message=_compose_message(definition, task, params or {}),
        role=getattr(parent_ctx, "role", None) or "student",
        is_teacher=bool(getattr(parent_ctx, "is_teacher", False)),
        grade_level=getattr(parent_ctx, "grade_level", None),
        depth=depth,
    )
    try:
        return await asyncio.wait_for(
            _consume(definition, req, child_deps), timeout=float(definition.timeout_s)
        )
    except asyncio.TimeoutError:
        return ToolResult(
            content=f"子 Agent {definition.name} 执行超时（>{definition.timeout_s}s），可重试。",
            card_type=definition.card_type,
            card_data={"agent": definition.name, "status": "timeout", "retryable": True},
        )
    except Exception as exc:  # noqa: BLE001 - 红线：错误结构化回注，禁止冒泡
        logger.warning("子 Agent %s 执行异常", definition.name, exc_info=True)
        return ToolResult(
            content=f"子 Agent {definition.name} 执行失败：{type(exc).__name__}（可重试或换个说法降级处理）",
            card_type=definition.card_type,
            card_data={"agent": definition.name, "status": "failed", "retryable": True},
        )
