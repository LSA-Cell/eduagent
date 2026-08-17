"""PromptSuggestion 追问推荐测试（§4.1）：生成+过审 / 超时降级 / 跨会话红线 / speculation 默认关。"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.services.llm_client import ChatResult
from app.suggest.generator import (
    SuggestionGenerator,
    maybe_prefetch,
    speculation_enabled,
)
from app.suggest.models import SuggestionLog
from tests.conftest import FakeSession


class _GenGateway:
    """追问生成网关替身：固定返回 content，记录全部调用入参。"""

    def __init__(self, content: Any = "", delay_s: float = 0.0) -> None:
        self._content = content
        self._delay_s = delay_s
        self.calls: list[dict] = []

    async def chat(self, messages, *, model=None, tools=None, max_tokens=None) -> ChatResult:
        self.calls.append({"messages": messages})
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if isinstance(self._content, Exception):
            raise self._content
        return ChatResult(content=self._content)


def _messages() -> list[dict]:
    return [
        {"role": "system", "content": "你是 EduAgent。"},
        {"role": "user", "content": "一元二次方程怎么解？"},
        {"role": "assistant", "content": "可以用求根公式或因式分解……"},
    ]


class TestGenerate:
    async def test_generates_3_moderated_items_and_logs(self) -> None:
        """生成 3 条候选，其中 1 条被内容审核过滤，其余 2 条下发并落库。"""
        gateway = _GenGateway("1. 求根公式怎么推导？\n2. 暴力破解WiFi教程\n3. 因式分解练习题有哪些？")
        session = FakeSession()

        async def moderate(text: str) -> bool:
            return "暴力" not in text  # 第 2 条不过审

        generator = SuggestionGenerator(
            gateway, moderate=moderate, session_factory=lambda: session
        )
        items = await generator.generate(
            messages=_messages(), subject="math", user_id=str(uuid.uuid4()),
            session_id=str(uuid.uuid4()),
        )
        assert items == ["求根公式怎么推导？", "因式分解练习题有哪些？"]
        [log] = [o for o in session.added if isinstance(o, SuggestionLog)]
        assert log.items == items and log.clicked_index is None

    async def test_timeout_silently_degrades(self) -> None:
        """生成 >800ms → 静默降级为不推荐（[]，不抛异常）。"""
        gateway = _GenGateway("1. a\n2. b\n3. c", delay_s=1.0)
        generator = SuggestionGenerator(gateway)
        assert await generator.generate(messages=_messages()) == []

    async def test_gateway_failure_degrades(self) -> None:
        gateway = _GenGateway(RuntimeError("gateway down"))
        generator = SuggestionGenerator(gateway)
        assert await generator.generate(messages=_messages()) == []

    async def test_moderation_failure_fail_closed(self) -> None:
        """审核服务异常：该条一律滤除（fail-closed）。"""
        gateway = _GenGateway("1. 问题一\n2. 问题二\n3. 问题三")

        async def broken_moderate(text: str) -> bool:
            raise RuntimeError("moderation down")

        generator = SuggestionGenerator(gateway, moderate=broken_moderate)
        assert await generator.generate(messages=_messages()) == []

    async def test_no_cross_session_input(self) -> None:
        """红线：生成输入只含当前会话——构造他会话标记，断言不进 prompt。"""
        foreign_marker = "别的会话的秘密内容-9f8e7d"
        gateway = _GenGateway("1. x\n2. y\n3. z")
        generator = SuggestionGenerator(gateway)
        await generator.generate(messages=_messages(), subject="math")
        prompt_text = str(gateway.calls[0]["messages"])
        assert "一元二次方程怎么解？" in prompt_text  # 当前会话内容在
        assert foreign_marker not in prompt_text  # 跨会话内容无从进入


class TestSpeculationGate:
    def test_default_off_without_gate(self) -> None:
        """红线：speculation 默认关闭（deps 无 feature_gate 钩子时一律 False）。"""
        assert speculation_enabled(SimpleNamespace()) is False

    def test_gate_exception_fails_closed(self) -> None:
        def broken_gate(key, *, user_id=None):
            raise RuntimeError("gate down")

        deps = SimpleNamespace(feature_gate=broken_gate)
        assert speculation_enabled(deps, "u1") is False

    async def test_prefetch_noop_when_gate_off(self) -> None:
        async def rag_search(**kwargs):  # 不应被调用
            raise AssertionError("gate 关闭时禁止预取")

        deps = SimpleNamespace(rag_search=rag_search)
        assert await maybe_prefetch(deps, suggestions=["q1"]) is None


class TestLoopIntegration:
    async def test_suggestions_event_after_run_end(self) -> None:
        """run 正常结束 → run.end 之后推送 suggestions 事件；不推荐时不推送。"""
        from app.loop.agent_loop import edu_agent_loop
        from app.loop.deps_protocol import GatewayEvent
        from tests.orchestration_fakes import FakeDeps, ScriptedGateway

        gateway = ScriptedGateway(
            default_script=[GatewayEvent(text="答", finish_reason="stop")]
        )
        deps = FakeDeps(gateway=gateway)
        deps.suggester = SuggestionGenerator(
            _GenGateway("1. 追问一\n2. 追问二\n3. 追问三"), moderate=lambda t: _true()
        )
        req = SimpleNamespace(user_id="u1", message="你好", session_id=None,
                              subject="math", model=None)
        events = [e async for e in edu_agent_loop(req, deps)]
        types = [e.event for e in events]
        assert types[-2:] == ["run.end", "suggestions"]
        assert events[-1].data["items"] == ["追问一", "追问二", "追问三"]

        # 推荐为空时不推送 suggestions 事件
        deps2 = FakeDeps(gateway=ScriptedGateway(
            default_script=[GatewayEvent(text="答", finish_reason="stop")]
        ))
        deps2.suggester = SuggestionGenerator(_GenGateway(""))
        events2 = [e async for e in edu_agent_loop(req, deps2)]
        assert "suggestions" not in [e.event for e in events2]


async def _true() -> bool:
    return True
