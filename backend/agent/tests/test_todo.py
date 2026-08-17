"""AgentTodo 可见任务清单测试（增强机制 §2.4，全部离线）。

红线对应：
- 多个 in_progress 并存被拒（协议校验）；
- 非多阶段任务上下文禁用 todo_write；
- 快照随 agent_runs 落库（audit write_run 的 todo_snapshot）；
- 全部 completed 前禁止产出最终交付卡片（card_gate）；
- 禁止跳状态（pending 直接 completed 被拒）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.loop.agent_loop import edu_agent_loop
from app.loop.deps_protocol import GatewayEvent
from app.todo.tool import TodoWriteArgs, TodoWriteTool, validate_transition

from tests.orchestration_fakes import FakeDeps, ScriptedGateway, make_registry, make_tool, tool_call


def _req(**overrides: Any) -> SimpleNamespace:
    base = dict(
        user_id="u1", message="组一份卷", session_id=None,
        subject="math", model=None, depth=2,  # 多阶段子 Agent 任务上下文
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def _collect(req: Any, deps: Any) -> list[Any]:
    return [event async for event in edu_agent_loop(req, deps)]


def _todo_script(items: list[dict[str, str]]) -> list[GatewayEvent]:
    return [
        GatewayEvent(
            tool_calls=[tool_call("todo_write", {"items": items}, call_id="c_todo")],
            finish_reason="tool_calls",
        )
    ]


_TEXT = [GatewayEvent(text="完成", finish_reason="stop")]


class TestTodoProtocol:
    def test_multiple_in_progress_rejected(self) -> None:
        """同一时间只允许 1 个 in_progress。"""
        with pytest.raises(ValidationError, match="in_progress"):
            TodoWriteArgs(items=[
                {"content": "阶段一", "status": "in_progress"},
                {"content": "阶段二", "status": "in_progress"},
            ])

    def test_skip_pending_to_completed_rejected(self) -> None:
        """禁止跳状态：pending 直接 completed 被拒。"""
        reason = validate_transition(
            [{"content": "阶段一", "status": "pending"}],
            TodoWriteArgs(items=[{"content": "阶段一", "status": "completed"}]).items,
            tools_executed_since_update=1,
        )
        assert reason and "pending" in reason

    def test_in_progress_to_completed_needs_tool_evidence(self) -> None:
        """in_progress → completed 需要期间有工具执行记录佐证。"""
        items = TodoWriteArgs(items=[{"content": "阶段一", "status": "completed"}]).items
        old = [{"content": "阶段一", "status": "in_progress"}]
        assert validate_transition(old, items, tools_executed_since_update=0) is not None
        assert validate_transition(old, items, tools_executed_since_update=1) is None


class TestTodoInLoop:
    async def test_rejected_outside_multistage_context(self) -> None:
        """普通问答（depth=1 且无多阶段 skill）禁用 todo_write。"""
        gateway = ScriptedGateway(stream_scripts=[
            _todo_script([{"content": "阶段一", "status": "in_progress"}]), _TEXT,
        ])
        deps = FakeDeps(gateway=gateway, registry=make_registry(TodoWriteTool()))
        events = await _collect(_req(depth=1), deps)
        tool_msgs = [
            m for m in gateway.stream_calls[1]["messages"] if m.get("role") == "tool"
        ]
        assert tool_msgs and "仅在多阶段任务" in tool_msgs[0]["content"]
        assert events[-1].data["finish_reason"] == "stop"

    async def test_snapshot_persisted_to_agent_runs(self) -> None:
        """每次更新推 todo_list 卡片；快照随 agent_runs 落库。"""
        items = [
            {"content": "分析考点分布", "status": "in_progress"},
            {"content": "组卷与校验", "status": "pending"},
        ]
        gateway = ScriptedGateway(stream_scripts=[_todo_script(items), _TEXT])
        deps = FakeDeps(gateway=gateway, registry=make_registry(TodoWriteTool()))

        events = await _collect(_req(), deps)

        cards = [e for e in events if e.event == "card"]
        assert cards and cards[0].data["card_type"] == "todo_list"
        assert cards[0].data["card_data"]["items"] == items
        final_run = deps.audit.runs[-1]
        assert final_run["status"] == "completed"
        assert final_run["todo_snapshot"] == items  # JSONB 快照落库

    async def test_delivery_card_blocked_until_todos_completed(self) -> None:
        """全部 completed 前禁止产出最终交付卡片（闸口拦截并回注原因）。"""
        items = [
            {"content": "分析考点分布", "status": "completed"},
            {"content": "组卷与校验", "status": "in_progress"},
        ]
        exam_tool = make_tool("exam_tool", card_type="exam_preview", is_read_only=False)
        gateway = ScriptedGateway(stream_scripts=[
            [GatewayEvent(
                tool_calls=[
                    tool_call("todo_write", {"items": items}, call_id="c_todo"),
                    tool_call("exam_tool", {"text": "组卷"}, call_id="c_exam"),
                ],
                finish_reason="tool_calls",
            )],
            _TEXT,
        ])
        deps = FakeDeps(gateway=gateway, registry=make_registry(TodoWriteTool(), exam_tool))

        events = await _collect(_req(), deps)

        # exam_preview 交付卡片被闸口拦截（todo_list 卡片仍正常下发）
        card_types = [e.data["card_type"] for e in events if e.event == "card"]
        assert "todo_list" in card_types
        assert "exam_preview" not in card_types
        tool_msgs = [
            m for m in gateway.stream_calls[1]["messages"] if m.get("role") == "tool"
        ]
        assert any("暂不产出" in m["content"] for m in tool_msgs)

    async def test_delivery_card_allowed_when_all_completed(self) -> None:
        """全部 completed 后交付卡片正常下发。"""
        items = [
            {"content": "分析考点分布", "status": "completed"},
            {"content": "组卷与校验", "status": "completed"},
        ]
        exam_tool = make_tool("exam_tool", card_type="exam_preview", is_read_only=False)
        gateway = ScriptedGateway(stream_scripts=[
            [GatewayEvent(
                tool_calls=[
                    tool_call("todo_write", {"items": items}, call_id="c_todo"),
                    tool_call("exam_tool", {"text": "组卷"}, call_id="c_exam"),
                ],
                finish_reason="tool_calls",
            )],
            _TEXT,
        ])
        deps = FakeDeps(gateway=gateway, registry=make_registry(TodoWriteTool(), exam_tool))

        events = await _collect(_req(), deps)

        card_types = [e.data["card_type"] for e in events if e.event == "card"]
        assert "exam_preview" in card_types
