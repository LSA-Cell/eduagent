"""消息优先级队列与打断语义测试（增强机制 §2.3，全部离线）。

红线对应：
- cancel 后工具任务真实取消（断言 asyncio.Task 收到 CancelledError）；
- 模型流真实中断（断言流被 aclose，且无后续 delta）；
- run.interrupted / tool.cancelled / message.queued / message.dequeued 事件；
- next 控制消息不被普通排队阻塞（block 工具长耗时也立即中断 run）；
- 工具未声明 interruptBehavior 默认 block（later 消息不取消它）；
- 半截消息 interrupted=true 落库。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Optional

import httpx
import pytest
from pydantic import BaseModel

from app.loop.agent_loop import edu_agent_loop
from app.loop.deps_protocol import GatewayEvent
from app.queue.message_queue import IncomingMessage, MessageQueue
from app.tools.base import EduToolBase, ToolResult

from tests.orchestration_fakes import FakeDeps, ScriptedGateway, make_registry, tool_call


class DeferredInbox:
    """收件箱 fake：工具/网关运行中经 push 注入消息，Loop 决策点 poll 取走。"""

    def __init__(self) -> None:
        self._pending: list[IncomingMessage] = []

    async def push(self, key: str, msg: IncomingMessage) -> None:
        self._pending.append(msg)

    async def poll(self, key: str) -> list[IncomingMessage]:
        out, self._pending = self._pending, []
        return out


class _SlowArgs(BaseModel):
    text: str = ""


def _make_slow_tool(
    name: str,
    *,
    inbox: DeferredInbox,
    inbox_key: str,
    interrupt_behavior: str = "block",
    sleep_s: float = 30.0,
    cancelled: Optional[list] = None,
    push_kind: str = "stop",
    push_priority: str = "next",
    push_content: str = "",
) -> EduToolBase:
    """执行开始后向收件箱注入一条消息，然后长时间挂起（等待被打断）。"""

    class _SlowTool(EduToolBase):
        pass

    tool = _SlowTool()
    tool.name = name  # type: ignore[misc]
    tool.description = "慢工具（测试打断）"  # type: ignore[misc]
    tool.input_model = _SlowArgs  # type: ignore[misc]
    tool.is_read_only = True  # type: ignore[misc]
    tool.is_concurrency_safe = False  # type: ignore[misc]
    tool.timeout_s = 60  # type: ignore[misc]
    tool.interrupt_behavior = interrupt_behavior  # type: ignore[misc]

    async def call(args: BaseModel, ctx: Any) -> ToolResult:
        await inbox.push(
            inbox_key,
            IncomingMessage(kind=push_kind, priority=push_priority, content=push_content),
        )
        try:
            await asyncio.sleep(sleep_s)
        except asyncio.CancelledError:
            if cancelled is not None:
                cancelled.append(True)
            raise
        return ToolResult(content="慢工具完成")

    tool.call = call  # type: ignore[misc]
    return tool


def _req(**overrides: Any) -> SimpleNamespace:
    base = dict(user_id="u1", message="开始", session_id="s-int", subject="math", model=None)
    base.update(overrides)
    return SimpleNamespace(**base)


async def _collect(req: Any, deps: Any) -> list[Any]:
    return [event async for event in edu_agent_loop(req, deps)]


def _types(events: list[Any]) -> list[str]:
    return [e.event for e in events]


class TestMessageQueue:
    def test_priority_order_and_fifo(self) -> None:
        """next > later；同级 FIFO。"""
        queue = MessageQueue()
        queue.enqueue(IncomingMessage(content="L1", priority="later", message_id="l1"))
        queue.enqueue(IncomingMessage(content="N1", priority="next", message_id="n1"))
        queue.enqueue(IncomingMessage(content="L2", priority="later", message_id="l2"))
        assert queue.pop().message_id == "n1"  # 控制类优先
        assert queue.pop().message_id == "l1"  # 同级 FIFO
        assert queue.pop().message_id == "l2"
        assert queue.pop() is None

    def test_cap_20_reject(self) -> None:
        """队列上限 20 条，超出拒绝。"""
        queue = MessageQueue()
        for i in range(20):
            assert queue.enqueue(IncomingMessage(content=f"m{i}")) is True
        assert queue.enqueue(IncomingMessage(content="溢出")) is False

    def test_queued_dequeued_events(self) -> None:
        seen: list[tuple[str, str]] = []
        queue = MessageQueue(on_event=lambda event, data: seen.append((event, data["message_id"])))
        queue.enqueue(IncomingMessage(content="x", priority="later", message_id="m1"))
        queue.pop()
        assert seen == [("message.queued", "m1"), ("message.dequeued", "m1")]


class TestToolInterruption:
    async def test_cancel_tool_truly_cancelled_on_stop(self) -> None:
        """interrupt_behavior=cancel 的工具：stop 到达 → asyncio.Task 真实取消。"""
        inbox = DeferredInbox()
        cancelled: list = []
        tool = _make_slow_tool(
            "slow_tool", inbox=inbox, inbox_key="s-int",
            interrupt_behavior="cancel", cancelled=cancelled,
        )
        gateway = ScriptedGateway(stream_scripts=[
            [GatewayEvent(tool_calls=[tool_call("slow_tool")], finish_reason="tool_calls")],
            [GatewayEvent(text="不应到达", finish_reason="stop")],
        ])
        deps = FakeDeps(gateway=gateway, registry=make_registry(tool))
        deps.inbox = inbox

        events = await _collect(_req(), deps)  # 若未真取消会挂到 sleep(30)

        types = _types(events)
        assert cancelled == [True]  # 工具协程收到 CancelledError（真实取消）
        assert "tool.cancelled" in types
        assert "run.interrupted" in types
        assert events[-1].data["finish_reason"] == "interrupted"
        # 中断后未再发起新一轮模型调用（禁止假取消后继续计费）
        assert len(gateway.stream_calls) == 1

    async def test_stop_not_blocked_by_block_tool(self) -> None:
        """next 控制消息任何时刻立即处理：block 工具长耗时也不阻塞停止。"""
        inbox = DeferredInbox()
        cancelled: list = []
        tool = _make_slow_tool(  # 未声明即默认 block
            "block_tool", inbox=inbox, inbox_key="s-int", cancelled=cancelled,
        )
        gateway = ScriptedGateway(stream_scripts=[
            [GatewayEvent(tool_calls=[tool_call("block_tool")], finish_reason="tool_calls")],
            [GatewayEvent(text="不应到达", finish_reason="stop")],
        ])
        deps = FakeDeps(gateway=gateway, registry=make_registry(tool))
        deps.inbox = inbox

        events = await _collect(_req(), deps)

        assert events[-1].data["finish_reason"] == "interrupted"
        assert "run.interrupted" in _types(events)
        assert cancelled == [True]  # run 级中断收尾：真实取消任务（非排队等 30s）

    async def test_later_message_waits_for_block_tool_then_steers(self) -> None:
        """later 消息：block（默认）工具不被取消，turn 收尾后 steer 消费。"""
        inbox = DeferredInbox()
        cancelled: list = []
        tool = _make_slow_tool(
            "block_tool", inbox=inbox, inbox_key="s-int",
            sleep_s=0.2, cancelled=cancelled,
            push_kind="text", push_priority="later", push_content="顺便讲讲平方差公式",
        )
        gateway = ScriptedGateway(stream_scripts=[
            [GatewayEvent(tool_calls=[tool_call("block_tool")], finish_reason="tool_calls")],
            [GatewayEvent(text="补充回答", finish_reason="stop")],
        ])
        deps = FakeDeps(gateway=gateway, registry=make_registry(tool))
        deps.inbox = inbox

        events = await _collect(_req(), deps)

        types = _types(events)
        assert cancelled == []  # block 工具未被取消
        assert "tool.cancelled" not in types
        assert "message.queued" in types and "message.dequeued" in types
        assert events[-1].data["finish_reason"] == "stop"
        # steer：第二轮模型请求包含追加消息
        assert len(gateway.stream_calls) == 2
        user_msgs = [m for m in gateway.stream_calls[1]["messages"] if m.get("role") == "user"]
        assert any("平方差公式" in str(m.get("content")) for m in user_msgs)


class InterruptibleGateway(ScriptedGateway):
    """流式网关：产出第 2 个 delta 后向收件箱注入 stop；记录 aclose。"""

    def __init__(self, inbox: DeferredInbox, inbox_key: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._inbox = inbox
        self._inbox_key = inbox_key
        self.closed = False

    async def chat_stream(self, messages, *, model=None, tools=None):
        self.stream_calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        script = self.stream_scripts.pop(0) if self.stream_scripts else self.default_script
        try:
            for index, event in enumerate(script):
                yield event
                if index == 1:  # 第 2 个 delta 后用户点停止
                    await self._inbox.push(
                        self._inbox_key,
                        IncomingMessage(kind="stop", priority="next"),
                    )
        finally:
            self.closed = True  # aclose 到达（模型流真实中断）


class TestStreamInterruption:
    async def test_stop_interrupts_model_stream(self) -> None:
        """流式中 stop：模型流真实中断（aclose），无后续 delta，run.interrupted。"""
        inbox = DeferredInbox()
        gateway = InterruptibleGateway(
            inbox,
            "s-int",
            stream_scripts=[[
                GatewayEvent(text="第1段"),
                GatewayEvent(text="第2段"),
                GatewayEvent(text="第3段"),
                GatewayEvent(text="第4段"),
                GatewayEvent(text="第5段", finish_reason="stop"),
            ]],
        )
        deps = FakeDeps(gateway=gateway)
        deps.inbox = inbox

        events = await _collect(_req(), deps)

        deltas = [e for e in events if e.event == "message.delta"]
        assert len(deltas) < 5  # 后续 delta 不再产出
        assert gateway.closed is True  # 模型流被真实关闭
        interrupted = [e for e in events if e.event == "run.interrupted"]
        assert interrupted and interrupted[0].data["reason"] == "stop_requested"
        assert events[-1].data["finish_reason"] == "interrupted"


class TestInterruptedPersistence:
    async def test_partial_message_persisted_interrupted_true(self) -> None:
        """半截消息 interrupted=true 落库（API 层经 run.interrupted 标记）。"""
        from app import deps as app_deps
        from app.main import create_app
        from eduagent_shared.models import Message
        from tests.conftest import FakeSession, auth_headers

        inbox = DeferredInbox()
        session = FakeSession()
        cancelled: list = []
        tool = _make_slow_tool(
            "block_tool", inbox=inbox, inbox_key="",  # session_id 由端点创建后回填 ctx
            cancelled=cancelled,
        )

        # 工具内 push 的 key 须等于运行 session_id：经 ctx 取
        async def call_with_ctx_key(args: BaseModel, ctx: Any) -> ToolResult:
            await inbox.push(
                str(ctx.session_id), IncomingMessage(kind="stop", priority="next")
            )
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
            return ToolResult(content="不应到达")

        tool.call = call_with_ctx_key  # type: ignore[misc]
        gateway = ScriptedGateway(stream_scripts=[
            [GatewayEvent(tool_calls=[tool_call("block_tool")], finish_reason="tool_calls")],
            [GatewayEvent(text="不应到达", finish_reason="stop")],
        ])
        orch_deps = FakeDeps(gateway=gateway, registry=make_registry(tool), session=session)
        orch_deps.inbox = inbox

        app = create_app()
        app.dependency_overrides[app_deps.get_orchestration_deps] = lambda: orch_deps

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat/stream",
                json={"user_id": "u-int", "message": "开始", "subject": "math"},
                headers=auth_headers(user_id="u-int"),
            )

        assert resp.status_code == 200
        assert cancelled == [True]
        frames = [f for f in resp.text.split("\n\n") if f.strip()]
        events = []
        for frame in frames:
            event_line = next(
                (l for l in frame.splitlines() if l.startswith("event:")), ""
            )
            data_line = next((l for l in frame.splitlines() if l.startswith("data:")), "")
            if event_line and data_line:
                events.append((event_line[6:].strip(), json.loads(data_line[5:].strip())))
        event_types = [t for t, _ in events]
        assert "run.interrupted" in event_types
        run_end = next(d for t, d in events if t == "run.end")
        assert run_end["finish_reason"] == "interrupted"
        # 落库断言：assistant 半截消息 metadata.interrupted == True
        assistant_msgs = [
            m for m in session.added
            if isinstance(m, Message) and m.role == "assistant"
        ]
        assert assistant_msgs, "应有落库的半截 assistant 消息"
        assert assistant_msgs[-1].metadata_.get("interrupted") is True
