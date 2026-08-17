"""Agent Loop 异步生成器：edu_agent_loop(req, deps)。

固定流水线（数据流 ④a–④g，顺序禁止调换）：
    L1 瘦身 → 压缩检查（microcompact → autocompact）→ 等待记忆预取注入 →
    经 deps.gateway 流式调模型（逐 token yield message.delta）→
    收集 tool_calls → 逐个过护栏链 → 分批执行（并发批 gather，写串行）→
    结果回注 → 无工具调用则退出。

三上限缺一不可：MAX_TURNS（默认 20）/ Token 预算 / 总时长。
三条恢复路径：max_output_tokens 自动续写（≤3 次）、prompt_too_long 响应式
压缩重试 1 次、同类失败连续 3 次熔断。Loop 内无阻塞调用。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, AsyncIterator, Optional

from app.context import autocompact, microcompact, reactive, slim, token_budget
from app.context.autocompact import CompactionError
from app.events.protocol import EventType, SSEEvent, SSESequencer
from app.loop import recovery
from app.loop.deps_protocol import Deps, PromptTooLongError
from app.loop.turn_config import TurnConfig
from app.loop.turn_state import TurnState
from app.memory import inject as memory_inject
from app.memory import prefetch as memory_prefetch
from app.loop.deps_protocol import ToolCall
from app.tools.base import ToolContext
from app.tools.executor import ToolCallRequest, ToolExecutor

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 20


def _base_system_prompt(config: TurnConfig) -> str:
    parts = ["你是 EduAgent，一名中小学全科学习助手。"]
    if config.subject:
        parts.append(f"当前学科：{config.subject}。")
    if config.grade_level:
        parts.append(f"学生年级：{config.grade_level}。")
    if config.role == "teacher":
        parts.append("当前用户身份：教师。")
    return "".join(parts)


async def _load_history(deps: Deps, config: TurnConfig) -> list[dict[str, Any]]:
    """加载会话最近消息（经注入的 session 工厂；失败降级空历史）。"""
    if not config.session_id:
        return []
    from app.services import chat_service

    try:
        async with deps.session_factory() as session:
            rows = await chat_service.load_recent_messages(
                session, config.session_id, limit=HISTORY_LIMIT
            )
        return chat_service.messages_to_prompt(rows)
    except Exception:  # noqa: BLE001 - 历史加载失败不阻塞本次对话
        logger.warning("会话历史加载失败，降级为空历史", exc_info=True)
        return []


def _assistant_tool_message(content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
    import json

    return {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in tool_calls
        ],
    }


async def edu_agent_loop(req: Any, deps: Deps) -> AsyncIterator[SSEEvent]:
    """Agent Loop 主入口（异步生成器，产出统一 SSE 事件）。"""
    config = TurnConfig.from_request(req)
    state = TurnState(run_id=uuid.uuid4().hex)
    sequencer = SSESequencer()
    pending: list[SSEEvent] = []  # emit 暂存区（tool/card/ask 等成相事件）

    def emit(event: EventType, **data: Any) -> None:
        pending.append(sequencer.make(event, run_id=state.run_id, **data))

    async def persist(event: SSEEvent) -> None:
        if deps.replay is not None:
            await deps.replay.append(state.run_id, event)

    async def drain() -> list[SSEEvent]:
        events, pending[:] = pending[:], []
        for event in events:
            await persist(event)
        return events

    async def audit_run(status: str) -> None:
        if deps.audit is None:
            return
        finished_at = None
        if status != "running":
            from datetime import datetime, timezone

            finished_at = datetime.now(timezone.utc)
        await deps.audit.write_run(
            run_id=state.run_id,
            session_id=config.session_id,
            user_id=config.user_id,
            status=status,
            turn_count=state.turn_count,
            model_route=config.model_route,
            token_usage={
                "tokens_used": state.tokens_used,
                "by_turn": [],  # 分 turn 明细见 tool_call_audits / context_compactions
            },
            finished_at=finished_at,
        )

    async def audit_compaction(record: dict[str, Any]) -> None:
        state.compactions.append(record)
        if deps.audit is not None:
            await deps.audit.write_compaction(session_id=config.session_id, **record)

    # ---- 启动：run.start → 记忆预取并发启动 → 历史加载 ---------------------
    start_event = sequencer.make(
        EventType.RUN_START, run_id=state.run_id, session_id=config.session_id
    )
    await persist(start_event)
    yield start_event
    await audit_run("running")

    message = str(getattr(req, "message", "") or "")
    prefetch_task = memory_prefetch.prefetch_memories(
        deps, config.user_id, message, subject=config.subject
    )
    deadline = time.monotonic() + config.max_duration_s

    history = await _load_history(deps, config)
    state.messages = (
        [{"role": "system", "content": _base_system_prompt(config)}]
        + history
        + [{"role": "user", "content": message}]
    )
    state.current_turn_start = len(state.messages) - 1

    ctx = ToolContext(
        user_id=config.user_id, session_id=config.session_id,
        deps=deps, run_id=state.run_id,
        role=config.role, is_teacher=config.is_teacher,
        subject=config.subject, grade_level=config.grade_level,
        depth=config.depth,  # 顶层 Loop=1；子 Agent 派发时 +1（红线：最大深度 2）
    )
    executor = ToolExecutor(
        deps.tool_registry, deps.guardrails, deps,
        event_sink=lambda event, data: emit(EventType(event), **data),
        audit=(
            (lambda fields: deps.audit.write_tool_call(run_id=state.run_id, **fields))
            if deps.audit is not None
            else None
        ),
    )
    tool_schemas = deps.tool_registry.openai_schemas(role=config.role)

    finish_reason = "stop"
    continuations = 0

    while True:
        # ---- 三上限检查 --------------------------------------------------
        if state.turn_count >= config.max_turns:
            emit(EventType.ERROR, code="max_turns",
                 message=f"已达单会话最大轮次（{config.max_turns}），请开启新会话继续。")
            finish_reason = "max_turns"
            break
        if state.tokens_used >= config.token_budget:
            emit(EventType.ERROR, code="token_budget", message="本轮对话 token 预算已用尽，请开启新会话。")
            finish_reason = "token_budget"
            break
        if time.monotonic() > deadline:
            emit(EventType.ERROR, code="timeout", message="本轮对话超时，请重试或开启新会话。")
            finish_reason = "timeout"
            break

        # ---- a. L1 工具结果瘦身（纯函数） --------------------------------
        slimmed, record = slim.slim_tool_results(
            state.messages,
            current_turn_start=state.current_turn_start,
            max_chars=config.slim_max_chars,
        )
        state.messages = slimmed
        if record:
            await audit_compaction(record)

        # ---- b. 压缩检查：L2 microcompact → L3 autocompact ---------------
        compacted, record = microcompact.microcompact(
            state.messages,
            current_turn_start=state.current_turn_start,
            keep_full=config.microcompact_keep,
        )
        state.messages = compacted
        if record:
            await audit_compaction(record)

        if token_budget.needs_autocompact(
            state.messages,
            window_tokens=config.model_window_tokens,
            buffer_tokens=config.compact_buffer_tokens,
        ):
            try:
                compacted, record = await autocompact.autocompact(
                    state.messages,
                    gateway=deps.gateway,
                    model=config.model_route,
                    current_turn_start=state.current_turn_start,
                )
            except CompactionError as exc:
                # 红线：禁止裸丢消息 —— 记失败（熔断计数），保留原消息继续，
                # 真超长时由 prompt_too_long → L4 响应式压缩兜底
                logger.warning("autocompact 失败: %s", exc)
                if recovery.record_failure(state, "compaction"):
                    emit(EventType.ERROR, code="circuit_broken",
                         message="上下文压缩连续失败，本次对话已熔断，请开启新会话。")
                    finish_reason = "circuit_broken"
                    for event in await drain():
                        yield event
                    break
            else:
                state.messages = compacted
                await audit_compaction(record)
                recovery.reset_failures(state)

        # ---- c. 等待记忆预取完成 → 注入 System Prompt（仅首次） ----------
        if not state.memory_injected:
            items = await memory_prefetch.await_with_timeout(
                prefetch_task, config.prefetch_timeout_ms
            )
            block, memory_ids = memory_inject.format_memory_block(
                items, max_tokens=config.memory_block_max_tokens
            )
            if block:
                state.messages[0] = {
                    **state.messages[0],
                    "content": state.messages[0]["content"] + "\n\n" + block,
                }
                state.memory_ids = memory_ids
                if memory_ids:
                    emit(EventType.MEMORY_USED, memory_ids=memory_ids)
            state.memory_injected = True
            for event in await drain():
                yield event

        # ---- d. 经推理网关流式调模型（逐 token 直推） --------------------
        turn_text = ""
        turn_tool_calls: list[ToolCall] = []
        turn_finish = "stop"
        try:
            stream = deps.gateway.chat_stream(
                state.messages, model=config.model_route,
                tools=tool_schemas or None,
            )
            async for event in stream:
                if event.text:
                    turn_text += event.text
                    delta = sequencer.make(
                        EventType.MESSAGE_DELTA, run_id=state.run_id, text=event.text
                    )
                    await persist(delta)
                    yield delta
                if event.finish_reason is not None:
                    turn_finish = event.finish_reason
                    turn_tool_calls = event.tool_calls or []
        except PromptTooLongError:
            # ---- 恢复路径 ②：响应式压缩后重试 1 次 ------------------------
            if recovery.can_reactive_retry(state):
                state.reactive_compact_used = True
                try:
                    compacted, record = await reactive.reactive_compact(
                        state.messages,
                        gateway=deps.gateway,
                        model=config.model_route,
                        current_turn_start=state.current_turn_start,
                    )
                except CompactionError:
                    emit(EventType.ERROR, code="prompt_too_long",
                         message="上下文过长且压缩失败，请开启新会话。")
                    finish_reason = "error"
                    for event in await drain():
                        yield event
                    break
                state.messages = compacted
                await audit_compaction(record)
                continue  # 重试 1 次（reactive_compact_used 保证不再重试）
            emit(EventType.ERROR, code="prompt_too_long",
                 message="上下文过长，压缩重试后仍失败，请开启新会话。")
            finish_reason = "error"
            for event in await drain():
                yield event
            break
        except Exception as exc:  # noqa: BLE001 - 网关异常 → 可读错误事件
            logger.warning("网关流式调用失败", exc_info=True)
            recovery.record_failure(state, "gateway")
            emit(EventType.ERROR, code="gateway_error",
                 message=f"模型服务暂不可用：{type(exc).__name__}")
            finish_reason = "error"
            for event in await drain():
                yield event
            break

        state.turn_count += 1

        # ---- 恢复路径 ①：max_output_tokens 自动续写（≤3 次） -------------
        while recovery.is_truncated(turn_finish) and recovery.can_continue(continuations):
            continuations += 1
            state.messages.append({"role": "assistant", "content": turn_text})
            state.messages.append({"role": "user", "content": recovery.CONTINUATION_PROMPT})
            state.current_turn_start = len(state.messages) - 1
            try:
                stream = deps.gateway.chat_stream(
                    state.messages, model=config.model_route, tools=tool_schemas or None
                )
                async for event in stream:
                    if event.text:
                        turn_text += event.text
                        delta = sequencer.make(
                            EventType.MESSAGE_DELTA, run_id=state.run_id, text=event.text
                        )
                        await persist(delta)
                        yield delta
                    if event.finish_reason is not None:
                        turn_finish = event.finish_reason
                        turn_tool_calls = event.tool_calls or []
            except Exception as exc:  # noqa: BLE001 - 续写失败以部分结果收尾
                logger.warning("续写失败，以部分结果收尾", exc_info=True)
                turn_finish = "stop"
                turn_tool_calls = []
                break
        if recovery.is_truncated(turn_finish) and not recovery.can_continue(continuations):
            emit(EventType.ERROR, code="max_continuations",
                 message="回复过长，已按部分结果收尾；如需完整内容请追问。")
            turn_finish = "stop"
            turn_tool_calls = []

        state.tokens_used += token_budget.estimate_text_tokens(turn_text)

        # ---- e/f. 工具调用：护栏链 → 分批执行 → 回注 ----------------------
        if not turn_tool_calls:
            state.messages.append({"role": "assistant", "content": turn_text})
            recovery.reset_failures(state)
            finish_reason = "stop"
            for event in await drain():
                yield event
            break

        state.messages.append(_assistant_tool_message(turn_text, turn_tool_calls))
        calls = [
            ToolCallRequest(id=c.id, name=c.name, arguments=c.arguments)
            for c in turn_tool_calls
        ]
        executions = await executor.execute(calls, config, ctx)
        state.tool_calls_total += len(executions)
        for event in await drain():
            yield event

        # ---- 结果回注消息历史 + 工具失败熔断 ----------------------------
        any_ok = False
        for execution in executions:
            state.messages.append({
                "role": "tool",
                "tool_call_id": execution.call.id,
                "name": execution.call.name,
                "content": execution.content,
            })
            state.tokens_used += token_budget.estimate_text_tokens(execution.content)
            if execution.card_type or execution.card_data:
                state.pending_cards.append({
                    "card_type": execution.card_type,
                    "card_data": execution.card_data,
                    "file_urls": execution.file_urls,
                })
            if execution.ok:
                any_ok = True
        if any_ok:
            recovery.reset_failures(state)
        elif executions and recovery.record_failure(state, "tool"):
            emit(EventType.ERROR, code="circuit_broken",
                 message="工具连续失败，本次对话已熔断；请稍后重试或开启新会话。")
            finish_reason = "circuit_broken"
            for event in await drain():
                yield event
            break

        # ---- g. 回到循环顶（无工具调用时已在上面 break） -----------------
        state.current_turn_start = len(state.messages)
        await asyncio.sleep(0)  # 让出事件循环（无阻塞调用红线）

    # ---- ⑤ 落库汇总：run.end + agent_runs -------------------------------
    for event in await drain():  # 上限熔断等路径暂存的 error 事件
        yield event
    usage = {
        "tokens_used": state.tokens_used,
        "turns": state.turn_count,
        "tool_calls": state.tool_calls_total,
        "compactions": len(state.compactions),
    }
    end_event = sequencer.make(
        EventType.RUN_END, run_id=state.run_id, finish_reason=finish_reason, usage=usage
    )
    await persist(end_event)
    yield end_event
    final_status = {
        "stop": "completed",
        "max_turns": "completed",
        "token_budget": "completed",
        "timeout": "failed",
        "error": "failed",
        "circuit_broken": "circuit_broken",
    }.get(finish_reason, "failed")
    await audit_run(final_status)
