"""工具执行器：护栏链 → 分批并发 → 超时 → 结果瘦身 → 审计。

红线落实：
- 入口强制过护栏链，代码上不存在免检通道；
- 仅 is_read_only && is_concurrency_safe 进并发批（asyncio.gather），写工具串行；
- 单工具超时 ≤ 工具声明 timeout_s（asyncio.wait_for），超时按失败回注；
- 超限结果存 tool-results/ bucket，content 替换为 ≤50 字摘要（§3.2
  ToolUseSummary）+ 预签名 URL + 全文长度，原始全文不再进入消息历史；storage 不可用时降级截断 + 标注；
- 每次调用落审计（注入回调，失败仅日志）。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.loop.partition import ToolBatch, partition_tool_calls
from app.tools.base import ToolContext, ToolError, ToolResult
from app.tools.registry import EduToolRegistry

logger = logging.getLogger(__name__)

TOOL_RESULTS_BUCKET = "tool-results"  # MinIO bucket 前缀（键含 run_id）

# 事件回调：同步收集 (event_type, data)，由 Loop 统一发 seq 并 yield
EventSink = Callable[[str, dict[str, Any]], None]
# 审计回调：失败仅日志（实现方保证 fail-soft）
AuditCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class ToolCallRequest:
    """一次待执行的工具调用（与网关 ToolCall 同构）。"""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolExecution:
    """一次工具调用的完整结果（回注内容 + 卡片 + 决策）。"""

    call: ToolCallRequest
    decision: str = "allow"  # allow/ask/deny
    ok: bool = False
    content: str = ""  # 回注消息历史用（已瘦身）
    card_type: Optional[str] = None
    card_data: Optional[dict[str, Any]] = None
    file_urls: list[str] = field(default_factory=list)
    latency_ms: int = 0
    stage: str = ""  # 护栏拦截环节（deny/ask 时）
    # Wave C：ask 挂起标记（Loop 见此后挂起等答复）与真实取消标记
    pending_ask: Optional[dict[str, Any]] = None
    cancelled: bool = False
    # Wave D1：≤50 字一句话摘要（§3.2，UI 展示与瘦身占位双用途）
    summary: str = ""


class ToolExecutor:
    """护栏 + 分批 + 并发 + 瘦身 + 审计的执行器。

    Wave C 扩展：
    - 工具任务句柄暴露在 _running（Loop 打断决策点据此真实取消）；
    - plan_mode（drafting/awaiting_confirm）期间仅放行只读工具 +
      PLAN_MODE_WRITE_EXEMPTIONS（submit_plan_outline）；
    - card_gate：最终交付卡片闸口（todo 全部 completed 前禁止产出）。
    """

    def __init__(
        self,
        registry: EduToolRegistry,
        guardrails: Any,  # GuardrailChain（鸭子类型，避免循环 import）
        deps: Any = None,  # Deps（storage 瘦身用，可为 None）
        *,
        event_sink: Optional[EventSink] = None,
        audit: Optional[AuditCallback] = None,
        card_gate: Optional[Callable[["ToolExecution"], Optional[str]]] = None,
        summary_stats: Optional[Any] = None,  # SummaryStats（§3.2 模板命中率计数）
        checkpoint_timer: Optional[Any] = None,  # CheckpointTimer（§5.1 guardrail 分段计时）
    ) -> None:
        self._registry = registry
        self._guardrails = guardrails
        self._deps = deps
        self._emit = event_sink or (lambda event, data: None)
        self._audit = audit
        self._card_gate = card_gate
        self._summary_stats = summary_stats
        self._checkpoint_timer = checkpoint_timer
        # 运行中工具任务句柄：call_id → (task, call)；打断决策据此取消
        self._running: dict[str, tuple[asyncio.Task, ToolCallRequest]] = {}

    # ---- Wave C：打断支持 ----------------------------------------------------
    @staticmethod
    def interrupt_behavior(tool: Any) -> str:
        """工具打断行为；未声明一律 block（红线：禁止隐式 cancel）。"""
        behavior = getattr(tool, "interrupt_behavior", None) or getattr(
            tool, "interruptBehavior", None
        )
        return behavior if behavior in ("cancel", "block") else "block"

    def running_calls(self) -> list[ToolCallRequest]:
        return [call for task, call in self._running.values() if not task.done()]

    async def cancel_running(self, *, only_cancellable: bool = True) -> list[str]:
        """取消运行中的工具任务（真实取消 asyncio.Task）。

        only_cancellable=True 时仅取消 interrupt_behavior="cancel" 的工具
        （block 工具排队等结束）；False 用于 run 级中断收尾（全部取消）。
        返回被取消的工具名（tool.cancelled 事件在 _run_one 收尾处推送）。
        """
        cancelled: list[str] = []
        for call_id, (task, call) in list(self._running.items()):
            if task.done():
                continue
            tool = self._registry.maybe_get(call.name)
            if only_cancellable and self.interrupt_behavior(tool) != "cancel":
                continue
            task.cancel()
            cancelled.append(call.name)
        return cancelled

    async def execute(
        self,
        calls: list[ToolCallRequest],
        config: Any,  # TurnConfig
        ctx: ToolContext,
    ) -> list[ToolExecution]:
        """按声明顺序执行全部调用，返回与原顺序一致的结果列表。"""
        results: dict[str, ToolExecution] = {}
        runnable: list[ToolCallRequest] = []

        # 1) 逐个过护栏链（未知工具直接失败回注，不放行）
        for call in calls:
            tool = self._registry.maybe_get(call.name)
            if tool is None:
                results[call.id] = ToolExecution(
                    call=call, decision="deny", stage="registry",
                    content=f"未注册的工具: {call.name}",
                )
                await self._audit_call(results[call.id], 0)
                continue
            timer = self._checkpoint_timer  # §5.1：guardrail 分段计时（None=不计）
            if timer is not None:
                timer.start("guardrail")
            try:
                decision = await self._guardrails.check(tool, config, call.arguments)
            finally:
                if timer is not None:
                    timer.stop("guardrail")
            if decision.decision == "allow":
                # Plan Mode 红线：drafting/awaiting_confirm 期间仅放行只读工具
                # （检索/计算/知识图谱）；submit_plan_outline 为唯一写例外
                if getattr(ctx, "plan_mode", False) and not getattr(tool, "is_read_only", False):
                    from app.plan.tool import PLAN_MODE_WRITE_EXEMPTIONS

                    if call.name not in PLAN_MODE_WRITE_EXEMPTIONS:
                        results[call.id] = ToolExecution(
                            call=call, decision="deny", stage="plan_mode",
                            content=(
                                f"工具 {call.name} 暂不可用：大纲尚未确认，"
                                "当前仅允许只读工具（检索/计算/知识图谱查询）；"
                                "请先提交大纲并等待用户确认。"
                            ),
                        )
                        await self._audit_call(results[call.id], 0)
                        continue
                runnable.append(call)
                continue
            if decision.decision == "ask":
                # 确认闭环（方案见 app/guardrails/pending.py docstring）：
                # 客户端确认后重发请求携带 confirm_token，此处 consume 一次性放行
                store = getattr(self._deps, "pending_confirms", None) if self._deps is not None else None
                confirm_token = getattr(config, "confirm_token", None)
                if store is not None and confirm_token and store.consume(
                    confirm_token, user_id=getattr(config, "user_id", ""), tool_name=call.name
                ):
                    runnable.append(call)
                    continue
                question = decision.reason or f"工具 {call.name} 需要确认后才能执行"
                token = uuid.uuid4().hex
                if store is not None:
                    token = store.register(
                        user_id=getattr(config, "user_id", ""),
                        tool_name=call.name,
                        arguments=call.arguments,
                        question=question,
                        run_id=getattr(ctx, "run_id", None),
                    )
                self._emit("guardrail.ask", {
                    "tool_name": call.name,
                    "question": question,
                    "confirm_token": token,
                })
                content = f"工具 {call.name} 需用户确认后执行（已向客户端发起确认请求）。"
            else:
                content = f"工具 {call.name} 被护栏拒绝：{decision.reason or '不允许的调用'}"
            results[call.id] = ToolExecution(
                call=call, decision=decision.decision, stage=decision.stage, content=content,
            )
            await self._audit_call(results[call.id], 0)

        # 2) 分批执行（并发批 gather / 写串行），单工具超时按失败回注
        for batch in partition_tool_calls(runnable, self._registry):
            await self._run_batch(batch, config, ctx, results)

        # 3) 保持原顺序返回
        return [results[call.id] for call in calls]

    async def _run_batch(
        self,
        batch: ToolBatch,
        config: Any,
        ctx: ToolContext,
        results: dict[str, ToolExecution],
    ) -> None:
        for call in batch.calls:
            self._emit("tool.start", {"tool_name": call.name, "display_name": call.name})
        # 统一包装为 asyncio.Task 并登记句柄（打断决策据此真实取消）
        tasks = [
            asyncio.create_task(self._tracked_run(call, config, ctx)) for call in batch.calls
        ]
        if batch.concurrent:
            executions = await asyncio.gather(*tasks)
        else:
            executions = [await task for task in tasks]
        for call, execution in zip(batch.calls, executions):
            results[call.id] = execution
            if execution.pending_ask is not None:
                continue  # ask 挂起标记由 Loop 处理，不进卡片事件
            if execution.card_type or execution.card_data:
                blocked = self._card_gate(execution) if self._card_gate else None
                if blocked is not None:
                    # 交付闸口拦截（如 todo 未完成）：卡片不下发，回注原因让模型继续
                    execution.card_type = None
                    execution.card_data = None
                    execution.file_urls = []
                    execution.content += f"\n[{blocked}]"
                    continue
                self._emit("card", {
                    "card_type": execution.card_type,
                    "card_data": execution.card_data,
                    "file_urls": execution.file_urls,
                })

    async def _tracked_run(
        self, call: ToolCallRequest, config: Any, ctx: ToolContext
    ) -> ToolExecution:
        self._running[call.id] = (asyncio.current_task(), call)  # type: ignore[arg-type]
        try:
            return await self._run_one(call, config, ctx)
        finally:
            self._running.pop(call.id, None)

    async def _run_one(
        self, call: ToolCallRequest, config: Any, ctx: ToolContext
    ) -> ToolExecution:
        """执行单个工具：入参校验 → wait_for 超时 → 结果瘦身 → 审计。"""
        tool = self._registry.get(call.name)
        started = time.monotonic()
        execution = ToolExecution(call=call)
        try:
            args = tool.input_model(**(call.arguments or {}))
        except Exception as exc:  # noqa: BLE001 - pydantic 校验错误回填给模型
            execution.content = f"工具 {call.name} 参数校验失败: {str(exc)[:200]}"
            execution.latency_ms = int((time.monotonic() - started) * 1000)
            await self._fill_summary(execution)
            await self._audit_call(execution, 0)
            return execution
        try:
            result: ToolResult = await asyncio.wait_for(
                tool.call(args, ctx), timeout=float(getattr(tool, "timeout_s", 10) or 10)
            )
        except asyncio.CancelledError:
            # 真实取消（打断决策）：不向上抛，收敛为取消结果回注 + tool.cancelled 事件
            execution.content = f"工具 {call.name} 已被取消（用户打断）。"
            execution.cancelled = True
            self._emit("tool.cancelled", {"tool_name": call.name})
        except asyncio.TimeoutError:
            execution.content = (
                f"工具 {call.name} 执行超时（>{getattr(tool, 'timeout_s', 10)}s），已按失败处理。"
            )
        except Exception as exc:  # noqa: BLE001 - 工具异常收敛为文本回注，不打断 Loop
            execution.content = f"工具 {call.name} 执行失败: {str(exc)[:300]}"
        else:
            execution.ok = True
            execution.card_type = result.card_type or getattr(tool, "card_type", None)
            execution.card_data = result.card_data
            execution.file_urls = list(result.file_urls)
            execution.pending_ask = getattr(result, "pending_ask", None)
            execution.content = await self._slim(
                result.content, tool, config, ctx, call
            )
        execution.latency_ms = int((time.monotonic() - started) * 1000)
        await self._fill_summary(execution)
        await self._audit_call(execution, len(execution.content))
        return execution

    async def _fill_summary(self, execution: ToolExecution) -> None:
        """生成 ≤50 字摘要（§3.2）；任何失败回退通用模板，禁止阻断执行。"""
        try:
            from app.tools.summary import summarize_tool_result

            execution.summary = await summarize_tool_result(
                execution.call.name,
                execution.call.arguments,
                execution.content,
                gateway=getattr(self._deps, "gateway", None),
                stats=self._summary_stats,
            )
        except Exception:  # noqa: BLE001 - 摘要失败仅降级为工具名占位
            logger.warning("工具摘要生成失败", exc_info=True)
            execution.summary = f"使用工具 {execution.call.name}"

    async def _slim(
        self,
        content: str,
        tool: Any,
        config: Any,
        ctx: ToolContext,
        call: ToolCallRequest,
    ) -> str:
        """超限结果瘦身：存 tool-results/ → 摘要 + 预签名 URL + 全文长度。

        storage 不可用/失败时降级为截断 + 标注；原始全文不再进入消息历史。
        """
        max_chars = min(
            int(getattr(tool, "max_result_chars", 20_000) or 20_000),
            int(getattr(config, "max_tool_result_chars", 20_000) or 20_000),
        )
        if len(content) <= max_chars:
            return content
        full_len = len(content)
        storage = getattr(self._deps, "storage", None) if self._deps is not None else None
        if storage is not None:
            key = f"{TOOL_RESULTS_BUCKET}/{ctx.run_id or 'norum'}/{call.id}.txt"
            try:
                await storage.put_text(key, content)
                url = await storage.presigned_get(key)
                # 瘦身占位前缀走 ToolUseSummary（§3.2：≤50 字元信息级摘要，
                # 禁止把原始全文前缀（可能含用户隐私原文）留在消息历史）
                from app.tools.summary import summarize_tool_result

                placeholder = await summarize_tool_result(
                    call.name, call.arguments, content,
                    gateway=getattr(self._deps, "gateway", None),
                    stats=self._summary_stats,
                )
                return (
                    f"{placeholder}\n"
                    f"[结果已瘦身：全文共 {full_len} 字符，完整内容见 {url}]"
                )
            except Exception:  # noqa: BLE001 - 存储失败降级截断
                logger.warning("工具结果瘦身存储失败，降级截断", exc_info=True)
        return f"{content[:max_chars]}\n[结果已截断：全文共 {full_len} 字符，存储不可用]"

    async def _audit_call(self, execution: ToolExecution, result_size: int) -> None:
        if self._audit is None:
            return
        try:
            await self._audit({
                "tool_name": execution.call.name,
                "input": execution.call.arguments,
                "decision": execution.decision,
                "latency_ms": execution.latency_ms,
                "result_size": result_size,
                "card_type": execution.card_type,
                "summary": execution.summary,  # §3.2 摘要（UI 展示/审计复用）
            })
        except Exception:  # noqa: BLE001 - 审计回调自身异常仅记日志
            logger.warning("工具审计回调失败", exc_info=True)


__all__ = [
    "ToolCallRequest",
    "ToolExecution",
    "ToolExecutor",
    "ToolError",
    "TOOL_RESULTS_BUCKET",
]
