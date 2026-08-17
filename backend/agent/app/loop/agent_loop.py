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

from app.context import autocompact, cache_aware, microcompact, reactive, slim, token_budget
from app.context.autocompact import CompactionError
from app.events.protocol import EventType, SSEEvent, SSESequencer
from app.loop import recovery
from app.loop.deps_protocol import Deps, PromptTooLongError
from app.loop.turn_config import TurnConfig
from app.loop.turn_state import TurnState
from app.memory import inject as memory_inject
from app.memory import prefetch as memory_prefetch
from app.loop.deps_protocol import ToolCall
from app.observe.checkpoint import CheckpointTimer, find_violations
from app.styles.registry import format_style_block
from app.tools.base import ToolContext
from app.tools.executor import ToolCallRequest, ToolExecutor

# Wave C：交互协作机制（均为可选注入；deps 缺对应属性时完全走旧路径）
from app.ask.policy import format_answers, resolve_timeout
from app.plan.manager import PLAN_SKILL_WHITELIST
from app.queue.message_queue import IncomingMessage, MessageQueue
from app.stop_hooks.base import StopHookContext
from app.stop_hooks.service import MAX_FORCED_CONTINUES, run_matched_hooks
from app.todo.tool import FINAL_DELIVERY_CARD_TYPES

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 20


def _base_system_prompt(config: TurnConfig, style_tone: Optional[str] = None) -> str:
    parts = ["你是 EduAgent，一名中小学全科学习助手。"]
    if config.subject:
        parts.append(f"当前学科：{config.subject}。")
    if config.grade_level:
        parts.append(f"学生年级：{config.grade_level}。")
    if config.role == "teacher":
        parts.append("当前用户身份：教师。")
    prompt = "".join(parts)
    if style_tone:
        # §5.3：<output_style> 固定区块（前缀稳定区；只影响表述，不影响事实与审核）
        prompt += "\n\n" + format_style_block(style_tone)
    return prompt


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
    checkpoint_timer = CheckpointTimer()  # Wave D2：分段打点（§5.1，开销 <5ms/run）
    checkpoint_timer.start("total")
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
            todo_snapshot=list(state.todo_items),  # Wave C：AgentTodo 快照落库
            timings=(
                checkpoint_timer.as_dict() if status != "running" else None
            ),  # Wave D2：分段计时落 agent_runs.timings（§5.1，只含毫秒计时）
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

    # ---- Wave D2：dream.summary 次日首启推送（§4.2.4，可选注入，fail-soft） --
    summary_provider = getattr(deps, "dream_summary_provider", None)
    if summary_provider is not None:
        try:
            dream_summary = await summary_provider(config.user_id)
        except Exception:  # noqa: BLE001 - 摘要推送失败不影响对话
            logger.warning("dream.summary 组装失败，跳过本次推送", exc_info=True)
            dream_summary = None
        if dream_summary:
            summary_event = sequencer.make(
                EventType.DREAM_SUMMARY, run_id=state.run_id,
                stats=dict(dream_summary.get("stats") or {}),
            )
            await persist(summary_event)
            yield summary_event

    # ---- Wave D2：Output Style 注入（§5.3，未命中不注入，前缀确定性） ------
    style_refresh = getattr(deps, "output_style_refresh", None)
    if style_refresh is not None:
        try:
            await style_refresh()  # 缓存 60s 热生效（DB 故障沿用旧缓存/不注入）
        except Exception:  # noqa: BLE001 - 刷新失败不阻塞对话
            logger.warning("output_style 缓存刷新失败", exc_info=True)
    style_lookup = getattr(deps, "output_style_lookup", None)
    style_tone = None
    if callable(style_lookup):
        try:
            style_tone = style_lookup(config.grade_level, config.subject)
        except Exception:  # noqa: BLE001 - 风格注册表故障不注入
            logger.warning("output_style 查询失败，本次不注入风格", exc_info=True)
            style_tone = None

    message = str(getattr(req, "message", "") or "")
    prefetch_task = memory_prefetch.prefetch_memories(
        deps, config.user_id, message, subject=config.subject
    )
    deadline = time.monotonic() + config.max_duration_s

    history = await _load_history(deps, config)
    state.messages = (
        [{"role": "system", "content": _base_system_prompt(config, style_tone)}]
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
    # ---- Wave C：运行时句柄注入（ask/todo/plan/stop-hook 协作用） -----------
    ctx.turn_state = state
    ctx.event_sink = lambda event, data: emit(EventType(event), **data)
    ctx.skill_code = config.skill_code
    # Plan Mode：白名单 skill 进入大纲两段式（drafting/awaiting_confirm 期间
    # 执行器仅放行只读工具 + submit_plan_outline，红线 §2.1.6）
    ctx.plan_mode = config.skill_code in PLAN_SKILL_WHITELIST

    def card_gate(execution: Any) -> Optional[str]:
        """交付闸口：todo 清单未全部 completed 前禁止产出最终交付卡片（§2.4）。"""
        if execution.card_type in FINAL_DELIVERY_CARD_TYPES and state.todo_items:
            if any(item.get("status") != "completed" for item in state.todo_items):
                return (
                    "任务清单尚未全部完成，最终交付卡片暂不产出；"
                    "请先用 todo_write 推进并逐项完成各阶段"
                )
        return None

    executor = ToolExecutor(
        deps.tool_registry, deps.guardrails, deps,
        event_sink=lambda event, data: emit(EventType(event), **data),
        audit=(
            (lambda fields: deps.audit.write_tool_call(run_id=state.run_id, **fields))
            if deps.audit is not None
            else None
        ),
        card_gate=card_gate,
        checkpoint_timer=checkpoint_timer,  # Wave D2：guardrail 分段计时（§5.1）
    )
    # ---- Wave D1：常驻工具集 + 当轮动态挂载（§4.3）/ 缓存前缀监控（§3.1） ----
    registry_prompt_schemas = getattr(deps.tool_registry, "prompt_schemas", None)

    def build_tool_schemas() -> list[dict[str, Any]]:
        """当轮可用工具 = 常驻集（+tool_search）+ 当轮动态挂载。"""
        if registry_prompt_schemas is not None:
            return registry_prompt_schemas(role=config.role, mounted=tuple(state.mounted_tools))
        return deps.tool_registry.openai_schemas(role=config.role)

    base_tool_schemas = build_tool_schemas()  # 常驻集：前缀稳定区的一部分
    on_cache_break = getattr(deps, "on_cache_break", None)  # §3.1 告警钩子

    async def cache_break_audit(record: dict[str, Any]) -> None:
        """缓存破坏审计：复用 context_compactions（level="cache_break"）。"""
        if deps.audit is not None:
            await deps.audit.write_compaction(session_id=config.session_id, **record)

    cache_watcher = cache_aware.CacheBreakWatcher(
        audit=cache_break_audit if deps.audit is not None else None,
        on_cache_break=on_cache_break,
        session_id=config.session_id,
        run_id=state.run_id,
    )

    async def guarded_apply(
        before: list[dict[str, Any]], after: list[dict[str, Any]], *, level: str
    ) -> list[dict[str, Any]]:
        """L1–L4 压缩结果应用闸口：改写前缀稳定区则拒绝应用+审计+告警。"""
        return await cache_aware.checked_apply(
            before, after, level=level,
            audit=cache_break_audit if deps.audit is not None else None,
            on_cache_break=on_cache_break,
            session_id=config.session_id,
        )

    # ---- Wave C：消息优先级队列 + 收件箱（打断/steer 决策点） ----------------
    inbox = getattr(deps, "inbox", None)  # None 时完全走旧路径（无打断源）
    inbox_key = config.session_id or state.run_id
    queue: MessageQueue = MessageQueue(
        on_event=lambda event, data: emit(EventType(event), **data)
    )
    interrupt_reason = ""

    async def poll_inbox() -> int:
        """从收件箱取新消息入队（next>later，上限 20 超出拒绝并提示）。"""
        if inbox is None:
            return 0
        arrived = 0
        for msg in await inbox.poll(inbox_key):
            arrived += 1
            if not queue.enqueue(msg):
                emit(EventType.ERROR, code="queue_full",
                     message="排队消息已达上限（20 条），请稍后再发送。")
        return arrived

    async def handle_control_messages() -> list[IncomingMessage]:
        """立即处理 next 类控制消息（禁止排队阻塞）；返回需 steer 的文本消息。"""
        steer: list[IncomingMessage] = []
        nonlocal interrupt_reason
        while (ctrl := queue.pop_next()) is not None:
            if ctrl.kind == "stop":
                state.interrupted = True
                interrupt_reason = "stop_requested"
            else:
                steer.append(ctrl)  # next 类文本：立即消费（不等 turn 结束）
        return steer

    def apply_steer(steer: list[IncomingMessage]) -> None:
        """把追加消息注入消息历史（steer 语义），下一个模型轮次看到。"""
        for msg in steer:
            state.messages.append({"role": "user", "content": f"[用户追加消息] {msg.content}"})
            if msg.content:
                state.current_turn_start = len(state.messages) - 1

    finish_reason = "stop"
    continuations = 0
    pending_steer: list[IncomingMessage] = []  # Wave C：待消费的追加消息（steer）

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

        # ---- Wave C：turn 边界消费点（next 立即处理；later 在此 steer） ----
        await poll_inbox()
        pending_steer.extend(await handle_control_messages())
        pending_steer.extend(queue.drain_later())
        if state.interrupted:
            for event in await drain():
                yield event
            break
        if pending_steer:
            apply_steer(pending_steer)
            pending_steer.clear()

        # ---- Wave D1：动态挂载仅当轮有效——跨轮自动卸载（§4.3 红线） ----
        if state.mounted_turn is not None and state.turn_count > state.mounted_turn:
            state.mounted_tools = []
            state.mounted_turn = None

        # ---- a. L1 工具结果瘦身（纯函数 + 前缀稳定区闸口） ---------------
        with checkpoint_timer.track("slim"):  # §5.1 分段打点
            slimmed, record = slim.slim_tool_results(
                state.messages,
                current_turn_start=state.current_turn_start,
                max_chars=config.slim_max_chars,
            )
            if record:
                applied = await guarded_apply(state.messages, slimmed, level="slim")
                if applied is not state.messages:
                    state.messages = applied
                    await audit_compaction(record)
            else:
                state.messages = slimmed

        # ---- b. 压缩检查：L2 microcompact → L3 autocompact ---------------
        with checkpoint_timer.track("compact"):  # §5.1 分段打点
            compacted, record = microcompact.microcompact(
                state.messages,
                current_turn_start=state.current_turn_start,
                keep_full=config.microcompact_keep,
            )
            if record:
                applied = await guarded_apply(state.messages, compacted, level="microcompact")
                if applied is not state.messages:
                    state.messages = applied
                    await audit_compaction(record)
            else:
                state.messages = compacted

        if token_budget.needs_autocompact(
            state.messages,
            window_tokens=config.model_window_tokens,
            buffer_tokens=config.compact_buffer_tokens,
        ):
            checkpoint_timer.start("compact")  # §5.1 分段打点
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
                applied = await guarded_apply(state.messages, compacted, level="autocompact")
                if applied is not state.messages:
                    state.messages = applied
                    await audit_compaction(record)
                    recovery.reset_failures(state)
            finally:
                checkpoint_timer.stop("compact")

        # ---- c. 等待记忆预取完成 → 注入 System Prompt（仅首次） ----------
        if not state.memory_injected:
            with checkpoint_timer.track("memory_prefetch"):  # §5.1 分段打点
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
        # Wave D1：每请求前前缀 hash 对比（§3.1 缓存破坏检测）；当轮工具
        # Schema = 常驻 + 动态挂载（mounted 属于前缀之后的后缀，不进 hash）
        tool_schemas = build_tool_schemas()
        await cache_watcher.observe(
            state.messages, base_tool_schemas,
            source=f"turn_{state.turn_count + 1}_request",
        )
        turn_text = ""
        turn_tool_calls: list[ToolCall] = []
        turn_finish = "stop"
        request_start = time.monotonic()  # §5.1：model_first_token 计时起点
        first_token_seen = False
        try:
            stream = deps.gateway.chat_stream(
                state.messages, model=config.model_route,
                tools=tool_schemas or None,
            )
            async for event in stream:
                if not first_token_seen:
                    first_token_seen = True
                    checkpoint_timer.add_ms(
                        "model_first_token",
                        (time.monotonic() - request_start) * 1000.0,
                    )
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
                # Wave C：流式中的打断决策点——next 类控制消息立即处理
                # （later 消息不打断流式，留待 turn 收尾后 steer 消费）
                await poll_inbox()
                if queue.peek_next() is not None:
                    pending_steer.extend(await handle_control_messages())
                    aclose = getattr(stream, "aclose", None)
                    if aclose is not None:
                        try:
                            await aclose()  # 真实中断模型流（禁止后端继续计费生成）
                        except Exception:  # noqa: BLE001 - 关闭失败不阻断打断
                            logger.warning("模型流关闭失败", exc_info=True)
                    break
            if state.interrupted:
                # 打断：半截助手消息以 interrupted=true 落库（API 层据
                # run.interrupted 事件标记），禁止以完整消息呈现
                if turn_text:
                    state.messages.append({"role": "assistant", "content": turn_text})
                for event in await drain():
                    yield event
                break
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
                applied = await guarded_apply(state.messages, compacted, level="reactive")
                if applied is not state.messages:
                    state.messages = applied
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

            # ---- Wave C：turn 收尾后消费队列（later 消息 steer 语义） ------
            await poll_inbox()
            pending_steer.extend(await handle_control_messages())
            pending_steer.extend(queue.drain_later())
            if state.interrupted:
                for event in await drain():
                    yield event
                break
            if pending_steer:
                apply_steer(pending_steer)
                pending_steer.clear()
                for event in await drain():
                    yield event
                continue

            # ---- Wave C：Stop Hooks 收尾校验（交付前质量闸口，§2.5） -------
            # 模型 stop 意图 → 匹配校验器；失败注入 blocking 消息强制继续
            # （≤2 次）；第 3 次仍失败 → quality_warning 卡片标记交付并落审计。
            hook_registry = getattr(deps, "stop_hooks", None)
            if hook_registry is not None:
                first_user = next(
                    (m for m in state.messages if m.get("role") == "user"),
                    {},
                )
                hook_ctx = StopHookContext(
                    answer_text=turn_text,
                    cards=tuple(state.pending_cards),
                    subject=config.subject,
                    skill_code=config.skill_code,
                    role=config.role,
                    user_text=str(first_user.get("content") or ""),  # 题面来源（blocking 注入不污染）
                )
                verdicts = await run_matched_hooks(
                    hook_registry, hook_ctx,
                    timeout_s=float(getattr(deps, "stop_hook_timeout_s", 2.0) or 2.0),
                )
                for verdict in verdicts:
                    if deps.audit is not None:
                        await deps.audit.write_stop_hook(
                            run_id=state.run_id,
                            hook_name=verdict.hook_name,
                            passed=verdict.passed,
                            detail={**verdict.detail, "elapsed_ms": verdict.elapsed_ms},
                            forced_continue_count=(
                                state.forced_continues if verdict.passed
                                else state.forced_continues + 1
                            ),
                        )
                failures = [v for v in verdicts if not v.passed]
                if failures and state.forced_continues < MAX_FORCED_CONTINUES:
                    state.forced_continues += 1
                    reasons = "；".join(v.reason for v in failures if v.reason)
                    state.messages.append({
                        "role": "user",
                        "content": (
                            f"交付前质量校验未通过（第 {state.forced_continues} 次）："
                            f"{reasons}"
                        ),
                    })
                    state.current_turn_start = len(state.messages) - 1
                    for event in await drain():
                        yield event
                    continue
                if failures:
                    # 第 3 次失败：带 warning 标记交付（前端可见标识，禁止静默降级）
                    emit(EventType.CARD, card_type="quality_warning", card_data={
                        "hooks": [v.hook_name for v in failures],
                        "reasons": [v.reason for v in failures],
                        "forced_continue_count": state.forced_continues,
                    }, file_urls=[])

            finish_reason = "stop"
            for event in await drain():
                yield event
            break

        state.messages.append(_assistant_tool_message(turn_text, turn_tool_calls))
        calls = [
            ToolCallRequest(id=c.id, name=c.name, arguments=c.arguments)
            for c in turn_tool_calls
        ]
        # Wave C：工具执行期间的打断决策点——轮询收件箱；新到消息触发
        # interruptBehavior 判定（cancel 类工具真实取消，block 排队等结束）；
        # next 停止立即生效（run 级中断取消全部运行中工具，禁止假取消）。
        exec_task = asyncio.create_task(executor.execute(calls, config, ctx))
        checkpoint_timer.start("tool_exec")  # §5.1 分段打点
        try:
            while not exec_task.done():
                await asyncio.wait({exec_task}, timeout=config.interrupt_poll_s)
                arrived = await poll_inbox()
                if arrived:
                    await executor.cancel_running(only_cancellable=True)
                pending_steer.extend(await handle_control_messages())
                if state.interrupted:
                    await executor.cancel_running(only_cancellable=False)
                    break
            executions = await exec_task
        finally:
            checkpoint_timer.stop("tool_exec")
        state.tool_calls_total += len(executions)
        for event in await drain():
            yield event

        # ---- Wave C：ask 挂起/恢复 ---------------------------------------
        # 挂起期间无任何模型调用（红线：禁止空转计费）；恢复只等用户答复或
        # 超时事件。超时按 app/ask/policy.py 分级：普通澄清→推荐项默认继续
        # 并明示；敏感决策→按取消（本 run 收尾）。
        for execution in executions:
            if execution.pending_ask is None or state.interrupted:
                continue
            ask = execution.pending_ask
            store = getattr(deps, "ask_store", None)
            answers = None
            if store is not None:
                answers = await store.wait(ask["ask_id"], timeout_s=config.ask_timeout_s)
            if answers is not None:
                execution.content = format_answers(ask["questions"], answers)
            else:
                outcome = resolve_timeout(ask["questions"])
                if outcome.cancelled:
                    state.cancelled = True
                    emit(EventType.ERROR, code="ask_timeout_cancelled",
                         message="敏感操作超时未确认，本次已按取消处理。")
                execution.content = outcome.content

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

        # ---- Wave C：取消/打断收尾 ---------------------------------------
        if state.cancelled:
            finish_reason = "cancelled"  # 敏感决策 ask 超时按取消
            for event in await drain():
                yield event
            break
        if state.interrupted:
            for event in await drain():
                yield event
            break

        # ---- g. 回到循环顶（无工具调用时已在上面 break） -----------------
        state.current_turn_start = len(state.messages)
        await asyncio.sleep(0)  # 让出事件循环（无阻塞调用红线）

    # ---- ⑤ 落库汇总：run.end + agent_runs -------------------------------
    if state.interrupted:
        # 打断通知（半截消息由 API 层以 interrupted=true 落库，禁止完整呈现）
        emit(EventType.RUN_INTERRUPTED, reason=interrupt_reason or "stop_requested")
        finish_reason = "interrupted"
    if state.cancelled and finish_reason != "cancelled":
        finish_reason = "cancelled"
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

    # ---- Wave D1：追问推荐（§4.1，仅 run 正常结束；失败静默降级不推荐） ----
    if finish_reason == "stop":
        suggester = getattr(deps, "suggester", None)
        if suggester is not None:
            try:
                suggestions = await suggester.generate(
                    messages=state.messages,
                    subject=config.subject,
                    user_id=config.user_id,
                    session_id=config.session_id,
                )
            except Exception:  # noqa: BLE001 - 推荐失败不影响主响应
                logger.warning("追问推荐挂接失败", exc_info=True)
                suggestions = []
            if suggestions:
                suggestions_event = sequencer.make(
                    EventType.SUGGESTIONS, run_id=state.run_id,
                    items=list(suggestions)[:3],
                )
                await persist(suggestions_event)
                yield suggestions_event

    final_status = {
        "stop": "completed",
        "max_turns": "completed",
        "token_budget": "completed",
        "timeout": "failed",
        "error": "failed",
        "circuit_broken": "circuit_broken",
        "interrupted": "interrupted",
        "cancelled": "cancelled",
    }.get(finish_reason, "failed")
    checkpoint_timer.stop("total")  # §5.1：全程计时收尾（重复 stop 静默忽略）
    await audit_run(final_status)

    # ---- Wave D2：p95 超阈值告警钩子（§5.1，阈值配置化，回调 fail-soft） ----
    on_checkpoint_alert = getattr(deps, "on_checkpoint_alert", None)
    if on_checkpoint_alert is not None:
        violations = find_violations(
            checkpoint_timer.as_dict(), config.checkpoint_thresholds_ms
        )
        if violations:
            try:
                on_checkpoint_alert({
                    "run_id": state.run_id, "user_id": config.user_id,
                    "violations": violations,
                })  # 只含计时计数，禁止携带消息内容
            except Exception:  # noqa: BLE001 - 告警回调异常不影响收尾
                logger.warning("checkpoint 告警回调失败", exc_info=True)

    # ---- Wave D1：成本台账（§3.4，写入失败降级补写+告警，禁止阻断） -------
    cost_recorder = getattr(deps, "cost_recorder", None)
    if cost_recorder is not None:
        try:
            await cost_recorder.record(
                run_id=state.run_id,
                user_id=config.user_id,
                model_route=config.model_route,
                input_tokens=token_budget.estimate_messages_tokens(state.messages),
                output_tokens=state.tokens_used,
                cache_hit_tokens=0,  # 网关未回传缓存命中明细前按 0 记（观测看板预留）
                quota_tier=config.quota_tier,
            )
        except Exception:  # noqa: BLE001 - 双保险（record 自身已不抛）
            logger.warning("成本记账挂接失败", exc_info=True)
