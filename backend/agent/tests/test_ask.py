"""AskUserQuestion 结构化中途提问测试（增强机制 §2.2，全部离线）。

红线对应：
- 挂起期间无模型调用（断言 gateway 计数在挂起前后不变）；
- 普通澄清超时 → 推荐项默认继续并在注入文本中明示；
- 敏感决策超时 → 按取消（finish_reason=cancelled）；
- 每 run 第 3 次 ask 被拒；连续两 turn 都 ask 被拒；
- 推荐项必须放首位且 label 追加"（推荐）"（协议校验）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

import pytest
from pydantic import ValidationError

from app.ask.tool import AskUserQuestionArgs, AskUserQuestionTool
from app.loop.agent_loop import edu_agent_loop
from app.loop.deps_protocol import GatewayEvent

from tests.orchestration_fakes import (
    FakeDeps,
    ScriptedGateway,
    make_registry,
    make_tool,
    tool_call,
)

QUESTIONS = [
    {
        "question": "想练哪种题型？",
        "options": [
            {"label": "选择题（推荐）", "description": "覆盖面广，适合快速检测"},
            {"label": "填空题", "description": "聚焦计算过程"},
        ],
        "multi_select": False,
    }
]
SENSITIVE_QUESTIONS = [
    {
        "question": "确认删除这份错题记录吗？",
        "options": [
            {"label": "保留（推荐）", "description": "继续保留在错题本"},
            {"label": "删除", "description": "不可恢复"},
        ],
        "multi_select": False,
        "sensitive": True,
    }
]


class ScriptedAskStore:
    """挂起存储 fake：register 记录问题；wait 返回脚本化答复（None=超时）。"""

    def __init__(self, answers: Optional[dict[str, Any]] = None) -> None:
        self._answers = answers
        self.registered: list[list[dict[str, Any]]] = []
        self.wait_call_count = 0

    async def register(self, *, user_id, run_id, questions, ttl_s=None):
        self.registered.append(questions)
        return f"ask-{len(self.registered)}"

    async def wait(self, ask_id: str, *, timeout_s: float):
        self.wait_call_count += 1
        return self._answers

    async def resolve(self, ask_id, answers, *, user_id=None):
        return True


def _req(**overrides: Any) -> SimpleNamespace:
    base = dict(user_id="u1", message="帮我出点题", session_id=None, subject="math", model=None)
    base.update(overrides)
    return SimpleNamespace(**base)


def _ask_script(questions: Any = None) -> list[GatewayEvent]:
    return [
        GatewayEvent(
            tool_calls=[tool_call("ask_user_question", {"questions": questions or QUESTIONS})],
            finish_reason="tool_calls",
        )
    ]


def _text_script(text: str = "好的，按选择出题。") -> list[GatewayEvent]:
    return [GatewayEvent(text=text, finish_reason="stop")]


def _make_deps(gateway: ScriptedGateway, store: ScriptedAskStore) -> FakeDeps:
    deps = FakeDeps(gateway=gateway, registry=make_registry(AskUserQuestionTool()))
    deps.ask_store = store
    return deps


async def _collect(req: Any, deps: Any) -> list[Any]:
    return [event async for event in edu_agent_loop(req, deps)]


class TestAskProtocol:
    def test_recommended_option_must_be_first(self) -> None:
        """推荐项约定：带"（推荐）"的选项不在首位 → 协议校验拒绝。"""
        with pytest.raises(ValidationError, match="推荐项必须放第一位"):
            AskUserQuestionArgs(questions=[{
                "question": "选哪种？",
                "options": [
                    {"label": "普通项", "description": ""},
                    {"label": "推荐项（推荐）", "description": ""},
                ],
            }])
        # 首位推荐 → 通过
        args = AskUserQuestionArgs(questions=[{
            "question": "选哪种？",
            "options": [
                {"label": "推荐项（推荐）", "description": ""},
                {"label": "普通项", "description": ""},
            ],
        }])
        assert args.questions[0].options[0].label.endswith("（推荐）")

    def test_question_count_and_option_bounds(self) -> None:
        with pytest.raises(ValidationError):
            AskUserQuestionArgs(questions=[{
                "question": "q",
                "options": [{"label": "仅一个", "description": ""}],  # 少于 2 个选项
            }])
        with pytest.raises(ValidationError):
            AskUserQuestionArgs(questions=[
                {"question": f"q{i}", "options": [
                    {"label": "a", "description": ""}, {"label": "b", "description": ""}
                ]}
                for i in range(4)  # 超过 3 题
            ])


class TestAskSuspendResume:
    async def test_no_model_calls_during_suspend(self) -> None:
        """挂起期间禁止模型空转：wait 前后 gateway 流式调用计数不变。"""
        answers = {"想练哪种题型？": ["选择题（推荐）"]}
        store = ScriptedAskStore(answers=answers)
        gateway = ScriptedGateway(stream_scripts=[_ask_script(), _text_script()])
        deps = _make_deps(gateway, store)

        events = await _collect(_req(), deps)

        assert len(gateway.stream_calls) == 2  # ask 前 1 次 + 恢复后 1 次，无空转
        assert store.wait_call_count == 1
        ask_events = [e for e in events if e.event == "ask"]
        assert len(ask_events) == 1
        assert ask_events[0].data["ask_id"] == "ask-1"
        assert ask_events[0].data["questions"][0]["options"][0]["label"] == "选择题（推荐）"
        # 答复已注入消息历史（第二轮模型请求的 tool 消息）
        tool_messages = [
            m for m in gateway.stream_calls[1]["messages"] if m.get("role") == "tool"
        ]
        assert tool_messages and "选择题（推荐）" in tool_messages[0]["content"]
        assert events[-1].data["finish_reason"] == "stop"

    async def test_normal_timeout_defaults_to_recommended(self) -> None:
        """普通澄清超时 → 按推荐项默认继续，注入文本明示。"""
        store = ScriptedAskStore(answers=None)  # 超时
        gateway = ScriptedGateway(stream_scripts=[_ask_script(), _text_script()])
        deps = _make_deps(gateway, store)

        events = await _collect(_req(), deps)

        assert events[-1].data["finish_reason"] == "stop"  # 继续正常完成
        tool_messages = [
            m for m in gateway.stream_calls[1]["messages"] if m.get("role") == "tool"
        ]
        assert tool_messages
        assert "超时未答复" in tool_messages[0]["content"]
        assert "推荐项默认值继续" in tool_messages[0]["content"]
        assert "选择题（推荐）（默认）" in tool_messages[0]["content"]  # 明示默认项

    async def test_sensitive_timeout_cancels(self) -> None:
        """敏感决策超时按取消：不再调模型，finish_reason=cancelled。"""
        store = ScriptedAskStore(answers=None)
        gateway = ScriptedGateway(stream_scripts=[_ask_script(SENSITIVE_QUESTIONS)])
        deps = _make_deps(gateway, store)

        events = await _collect(_req(), deps)

        assert len(gateway.stream_calls) == 1  # 取消后无后续模型调用
        error_events = [e for e in events if e.event == "error"]
        assert error_events and error_events[0].data["code"] == "ask_timeout_cancelled"
        assert events[-1].data["finish_reason"] == "cancelled"


class TestAskFrequency:
    async def test_third_ask_rejected(self) -> None:
        """每 run ask ≤2 次：第 3 次回注拒绝（须合并问题或直接作答）。"""
        store = ScriptedAskStore(answers={"想练哪种题型？": ["填空题"]})
        echo = [GatewayEvent(tool_calls=[tool_call("echo_tool")], finish_reason="tool_calls")]
        gateway = ScriptedGateway(stream_scripts=[
            _ask_script(), echo, _ask_script(), echo, _ask_script(), _text_script("收尾"),
        ])
        deps = FakeDeps(
            gateway=gateway,
            registry=make_registry(AskUserQuestionTool(), make_tool("echo_tool")),
        )
        deps.ask_store = store

        events = await _collect(_req(), deps)

        assert len(store.registered) == 2  # 第 3 次未登记（被拒）
        last_messages = gateway.stream_calls[-1]["messages"]
        tool_messages = [m for m in last_messages if m.get("role") == "tool"]
        assert any("上限" in m["content"] for m in tool_messages)
        assert events[-1].data["finish_reason"] == "stop"

    async def test_consecutive_turn_ask_rejected(self) -> None:
        """禁止连续两个 turn 都 ask。"""
        store = ScriptedAskStore(answers={"想练哪种题型？": ["填空题"]})
        gateway = ScriptedGateway(stream_scripts=[
            _ask_script(), _ask_script(), _text_script("收尾"),
        ])
        deps = _make_deps(gateway, store)

        events = await _collect(_req(), deps)

        assert len(store.registered) == 1  # 第二次连续追问被拒
        last_messages = gateway.stream_calls[-1]["messages"]
        tool_messages = [m for m in last_messages if m.get("role") == "tool"]
        assert any("禁止连续追问" in m["content"] for m in tool_messages)
