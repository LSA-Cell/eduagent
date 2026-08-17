"""Agent Loop 测试：事件序列、三上限熔断、三条恢复路径（全部离线 fake deps）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.loop.agent_loop import edu_agent_loop
from app.loop.deps_protocol import GatewayEvent, PromptTooLongError

from tests.orchestration_fakes import (
    FakeDeps,
    ScriptedGateway,
    make_registry,
    make_tool,
    tool_call,
)


def _req(**overrides):
    """聊天请求鸭子类型（SimpleNamespace，便于携带 max_turns 等调优参数）。"""
    base = dict(user_id="u1", message="你好", session_id=None, subject="math", model=None)
    base.update(overrides)
    return SimpleNamespace(**base)


async def _collect(req, deps):
    return [event async for event in edu_agent_loop(req, deps)]


def _types(events):
    return [e.event for e in events]


class TestEventSequence:
    async def test_tool_then_text_event_sequence(self) -> None:
        """第一轮返回 tool_calls、第二轮纯文本：run.start→tool.start→card→delta*→run.end。"""
        calls: list = []
        registry = make_registry(make_tool("echo_tool", card_type="grading", calls=calls))
        gateway = ScriptedGateway(stream_scripts=[
            [GatewayEvent(tool_calls=[tool_call("echo_tool", {"text": "hi"}, "c1")],
                          finish_reason="tool_calls")],
            [GatewayEvent(text="你"), GatewayEvent(text="好"), GatewayEvent(finish_reason="stop")],
        ])
        deps = FakeDeps(gateway=gateway, registry=registry)

        events = await _collect(_req(), deps)

        assert _types(events) == [
            "run.start", "tool.start", "card", "message.delta", "message.delta", "run.end",
        ]
        seqs = [e.seq for e in events]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)  # 单调递增
        assert all(e.data.get("run_id") for e in events)
        assert calls and calls[0]["args"] == {"text": "hi"}
        assert events[2].data["card_type"] == "grading"
        assert events[-1].data["finish_reason"] == "stop"
        assert events[-1].data["usage"]["tool_calls"] == 1
        # 工具结果已回注进第二轮模型请求
        second_call_messages = gateway.stream_calls[1]["messages"]
        tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
        assert tool_messages and "echo:hi" in tool_messages[0]["content"]

    async def test_plain_text_run(self) -> None:
        gateway = ScriptedGateway(default_script=[GatewayEvent(text="答", finish_reason="stop")])
        deps = FakeDeps(gateway=gateway)
        events = await _collect(_req(), deps)
        assert _types(events) == ["run.start", "message.delta", "run.end"]


class TestCircuitLimits:
    async def test_max_turns_breaker(self) -> None:
        """模型每轮都发起工具调用：max_turns=3 熔断，工具恰好执行 3 次。"""
        registry = make_registry(make_tool("echo_tool"))
        gateway = ScriptedGateway(default_script=[
            GatewayEvent(tool_calls=[tool_call("echo_tool")], finish_reason="tool_calls")
        ])
        deps = FakeDeps(gateway=gateway, registry=registry)

        events = await _collect(_req(max_turns=3), deps)

        assert events[-1].event == "run.end"
        assert events[-1].data["finish_reason"] == "max_turns"
        assert "max_turns" in [e.data.get("code") for e in events if e.event == "error"]
        assert len(deps.audit.tool_calls) == 3

    async def test_token_budget_breaker(self) -> None:
        registry = make_registry(make_tool("echo_tool"))
        gateway = ScriptedGateway(default_script=[
            GatewayEvent(tool_calls=[tool_call("echo_tool")], finish_reason="tool_calls")
        ])
        deps = FakeDeps(gateway=gateway, registry=registry)

        events = await _collect(_req(token_budget=1), deps)

        assert events[-1].data["finish_reason"] == "token_budget"
        assert any(
            e.event == "error" and e.data.get("code") == "token_budget" for e in events
        )

    async def test_tool_failure_circuit_breaker(self) -> None:
        """同类失败（工具异常）连续 3 次熔断，禁止静默重试。"""
        registry = make_registry(make_tool("bad_tool", result=RuntimeError("boom")))
        gateway = ScriptedGateway(default_script=[
            GatewayEvent(tool_calls=[tool_call("bad_tool")], finish_reason="tool_calls")
        ])
        deps = FakeDeps(gateway=gateway, registry=registry)

        events = await _collect(_req(), deps)

        assert events[-1].data["finish_reason"] == "circuit_broken"
        assert any(
            e.event == "error" and e.data.get("code") == "circuit_broken" for e in events
        )
        assert len(deps.audit.tool_calls) == 3  # 熔断阈值 3，不多烧


class TestRecoveryPaths:
    @staticmethod
    async def _seed_history(fake_session, n: int = 8) -> None:
        """写入长历史消息，让响应式压缩有内容可压（当前 turn 消息受保护）。"""
        from eduagent_shared.models import Message

        for i in range(n):
            fake_session.add(Message(
                session_id="s1",
                role="user" if i % 2 == 0 else "assistant",
                content=f"历史消息{i}" * 100,
            ))
        await fake_session.flush()

    async def test_prompt_too_long_reactive_retry_once(self, fake_session) -> None:
        """prompt_too_long → 响应式压缩后重试 1 次并成功。"""
        await self._seed_history(fake_session)
        gateway = ScriptedGateway(stream_scripts=[
            PromptTooLongError("context too long"),
            [GatewayEvent(text="恢复", finish_reason="stop")],
        ])
        deps = FakeDeps(gateway=gateway, session=fake_session)

        events = await _collect(_req(session_id="s1"), deps)

        assert len(gateway.stream_calls) == 2  # 原请求 + 压缩后重试
        assert "message.delta" in _types(events)
        assert events[-1].data["finish_reason"] == "stop"
        assert [c["level"] for c in deps.audit.compactions] == ["reactive"]
        # 压缩后消息变少（摘要边界替换历史）
        assert len(gateway.stream_calls[1]["messages"]) < len(gateway.stream_calls[0]["messages"])

    async def test_prompt_too_long_retry_still_fails(self, fake_session) -> None:
        """重试仍 prompt_too_long → 终止 turn，提示开启新会话。"""
        await self._seed_history(fake_session)
        gateway = ScriptedGateway(stream_scripts=[
            PromptTooLongError("context too long"),
            PromptTooLongError("still too long"),
        ])
        deps = FakeDeps(gateway=gateway, session=fake_session)

        events = await _collect(_req(session_id="s1"), deps)

        assert len(gateway.stream_calls) == 2  # 只重试 1 次
        assert events[-1].data["finish_reason"] == "error"
        assert any(
            e.event == "error" and e.data.get("code") == "prompt_too_long" for e in events
        )

    async def test_max_output_tokens_auto_continuation(self) -> None:
        """finish_reason=length 触发自动续写，最多 3 次后以部分结果收尾。"""
        gateway = ScriptedGateway(stream_scripts=[
            [GatewayEvent(text="第一段", finish_reason="length")],
            [GatewayEvent(text="第二段", finish_reason="length")],
            [GatewayEvent(text="第三段", finish_reason="length")],
            [GatewayEvent(text="第四段", finish_reason="length")],  # 不会被消费（≤3 次续写）
        ])
        deps = FakeDeps(gateway=gateway)

        events = await _collect(_req(), deps)

        text = "".join(e.data["text"] for e in events if e.event == "message.delta")
        assert text == "第一段第二段第三段第四段"
        assert len(gateway.stream_calls) == 4  # 首发 + 3 次续写
        assert events[-1].data["finish_reason"] == "stop"
        assert any(
            e.event == "error" and e.data.get("code") == "max_continuations" for e in events
        )
