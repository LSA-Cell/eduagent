"""子 Agent 编排测试：loader 校验 / 同步 runner / celery_bridge 幂等（全部离线）。"""

from __future__ import annotations

import pytest

from app.loop.deps_protocol import GatewayEvent
from app.subagents.celery_bridge import TaskDedup, dispatch_async, make_task_id
from app.subagents.loader import (
    SubAgentDefinition,
    SubAgentError,
    load_definitions_dir,
    load_registry,
    merge_db_rows,
)
from app.subagents.runner import (
    MAX_SUBAGENT_DEPTH,
    build_child_registry,
    route_model,
    run_sync_subagent,
)
from app.tasks.subagent_tasks import run_subagent_async
from app.tools.base import ToolContext
from tests.conftest import FakeSession, as_factory
from tests.orchestration_fakes import (
    FakeDeps,
    ScriptedGateway,
    make_registry,
    make_tool,
)


class TestLoader:
    def test_builtin_definitions_loaded(self) -> None:
        """内置 6 个子 Agent（规格 4+1 + learning_analysis 补位教师工具）。"""
        registry = load_registry()
        assert registry.names() == [
            "exam_paper_agent", "grading_agent", "learning_analysis_agent",
            "lesson_plan_agent", "oral_scoring_agent", "ppt_agent",
        ]
        grading = registry.get("grading_agent")
        assert grading.async_via == "celery"
        assert grading.card_type == "grading"
        assert "ocr_extract" in grading.tools_allowlist
        assert registry.get("oral_scoring_agent").async_via == "none"
        # 教师专属门槛自描述（§3.2 role_required="teacher" 落到 dispatch 处）
        assert registry.get("ppt_agent").role_required == "teacher"

    def test_invalid_definition_rejected(self, tmp_path) -> None:
        (tmp_path / "bad.yaml").write_text(
            "name: BadName\ndescription: x\nasync_via: celery\n", encoding="utf-8"
        )
        with pytest.raises(SubAgentError):
            load_definitions_dir(tmp_path)

    def test_invalid_async_via_rejected(self, tmp_path) -> None:
        (tmp_path / "bad.yaml").write_text(
            "name: bad_agent\ndescription: x\nasync_via: threads\n", encoding="utf-8"
        )
        with pytest.raises(SubAgentError):
            load_definitions_dir(tmp_path)

    def test_duplicate_name_rejected(self, tmp_path) -> None:
        for suffix in ("a", "b"):
            (tmp_path / f"{suffix}.yaml").write_text(
                "name: dup_agent\ndescription: x\nasync_via: none\n", encoding="utf-8"
            )
        with pytest.raises(SubAgentError):
            load_definitions_dir(tmp_path)

    def test_merge_db_rows_override_and_deactivate(self) -> None:
        base = load_definitions_dir()
        merged = merge_db_rows(base, [
            {"name": "grading_agent", "description": "DB 覆盖的描述",
             "async_via": "celery", "tools_allowlist": ["calculator"]},
            {"name": "ppt_agent", "description": "下架", "async_via": "celery",
             "is_active": False},
        ])
        assert merged["grading_agent"].description == "DB 覆盖的描述"
        assert "ppt_agent" not in merged  # is_active=false 下架

    def test_merge_db_rows_preserves_role_gate(self) -> None:
        """安全网：DB 行覆盖时不得丢掉 YAML 基线的 role_required。"""
        base = load_definitions_dir()
        merged = merge_db_rows(base, [
            {"name": "ppt_agent", "description": "DB 版", "async_via": "celery"},
        ])
        assert merged["ppt_agent"].role_required == "teacher"


class TestRunner:
    def _definition(self, **overrides) -> SubAgentDefinition:
        base = {
            "name": "demo_agent",
            "description": "演示子 Agent",
            "subject_routing": {"math": "edu-math-7b", "default": "edu-general-14b"},
            "tools_allowlist": ["echo_tool"],
            "async_via": "none",
            "timeout_s": 10,
        }
        base.update(overrides)
        return SubAgentDefinition(**base)

    def test_build_child_registry_trims(self) -> None:
        """裁剪工具池：allowlist ∩ 父注册表；dispatch 无条件剔除；幽灵工具跳过。"""
        parent = make_registry(make_tool("echo_tool"), make_tool("secret_tool"))
        definition = self._definition(
            tools_allowlist=["echo_tool", "sub_agent_dispatch", "ghost_tool"]
        )
        child = build_child_registry(parent, definition)
        assert child.names() == ["echo_tool"]  # secret/dispatch/ghost 都进不来

    def test_route_model_subject_then_default(self) -> None:
        definition = self._definition()
        assert route_model(definition, "math") == "edu-math-7b"
        assert route_model(definition, "english") == "edu-general-14b"
        assert route_model(self._definition(subject_routing={}), "math") is None

    async def test_sync_run_isolated_context(self) -> None:
        """独立 messages/run_id：子 Loop 只见组装消息，不见父会话历史。"""
        gateway = ScriptedGateway(
            default_script=[GatewayEvent(text="子结果", finish_reason="stop")]
        )
        deps = FakeDeps(
            gateway=gateway,
            registry=make_registry(make_tool("echo_tool"), make_tool("secret_tool")),
        )
        ctx = ToolContext(user_id="u1", deps=deps, role="student")

        result = await run_sync_subagent(
            self._definition(), task="完成任务", params={"k": 1},
            subject="math", deps=deps, parent_ctx=ctx, depth=2,
        )

        assert "子结果" in result.content
        assert result.card_data["agent"] == "demo_agent"
        messages = gateway.stream_calls[0]["messages"]
        assert len(messages) == 2  # system + 组装的 user，无父历史
        assert messages[-1]["role"] == "user"
        assert "完成任务" in messages[-1]["content"]
        # 工具池已裁剪：下发给子模型的 schema 只有 echo_tool
        tools = gateway.stream_calls[0]["tools"]
        assert [t["function"]["name"] for t in tools] == ["echo_tool"]

    async def test_error_structured_not_raised(self) -> None:
        """子 Agent 失败回注结构化错误（可重试），异常不冒泡。"""
        gateway = ScriptedGateway(stream_scripts=[RuntimeError("网关炸了")])
        deps = FakeDeps(gateway=gateway, registry=make_registry(make_tool("echo_tool")))
        ctx = ToolContext(user_id="u1", deps=deps)

        result = await run_sync_subagent(
            self._definition(), task="t", params=None,
            subject=None, deps=deps, parent_ctx=ctx, depth=2,
        )

        assert "失败" in result.content
        assert result.card_data["status"] == "failed"
        assert result.card_data["retryable"] is True

    async def test_depth_limit(self) -> None:
        gateway = ScriptedGateway()
        deps = FakeDeps(gateway=gateway)
        ctx = ToolContext(user_id="u1", deps=deps)
        result = await run_sync_subagent(
            self._definition(), task="t", params=None,
            subject=None, deps=deps, parent_ctx=ctx, depth=MAX_SUBAGENT_DEPTH + 1,
        )
        assert result.card_data["status"] == "depth_exceeded"
        assert gateway.stream_calls == []  # 未触达模型


class TestCeleryBridge:
    def test_task_id_stable_for_same_args(self) -> None:
        a = make_task_id("ppt_agent", "u1", "做课件", {"topic": "x", "grade": "g"})
        b = make_task_id("ppt_agent", "u1", "做课件", {"grade": "g", "topic": "x"})
        c = make_task_id("ppt_agent", "u1", "做课件", {"topic": "y"})
        assert a == b and a != c  # 参数序无关；参数变则键变

    async def test_dispatch_dedup(self) -> None:
        """同参数重复派发：第二次返回 duplicate，celery 只投递一次。"""
        deps = FakeDeps(session=FakeSession())
        registry = load_registry()
        definition = registry.get("ppt_agent")
        ctx = ToolContext(user_id="t1", deps=deps, role="teacher", is_teacher=True)
        params = {"topic": "二次函数", "grade": "初三", "subject": "math"}

        first = await dispatch_async(
            definition, task="做课件", params=params, subject="math", deps=deps, ctx=ctx
        )
        second = await dispatch_async(
            definition, task="做课件", params=params, subject="math", deps=deps, ctx=ctx
        )

        assert first.card_data["status"] == "accepted"
        assert second.card_data["status"] == "duplicate"
        assert second.card_data["task_id"] == first.card_data["task_id"]
        assert len(deps.celery_sent) == 1

    async def test_generic_agent_goes_to_subagent_task(self) -> None:
        """grading_agent 走通用子 Agent 任务（agent.subagent.run），带幂等 task_id。"""
        deps = FakeDeps()
        definition = load_registry().get("grading_agent")
        ctx = ToolContext(user_id="u1", deps=deps)

        result = await dispatch_async(
            definition, task="批改作业", params={"images": ["a.png"]},
            subject="math", deps=deps, ctx=ctx,
        )

        assert result.card_data["status"] == "accepted"
        task_name, payload = deps.celery_sent[0]
        assert task_name == "agent.subagent.run"
        assert payload["agent"] == "grading_agent"
        assert payload["task_id"] == result.card_data["task_id"]
        assert payload["user_id"] == "u1"

    async def test_invalid_params_structured_error(self) -> None:
        """参数不足（缺 topic）：结构化错误回注，不投递、不冒泡。"""
        deps = FakeDeps(session=FakeSession())
        definition = load_registry().get("ppt_agent")
        ctx = ToolContext(user_id="t1", deps=deps, role="teacher", is_teacher=True)

        result = await dispatch_async(
            definition, task="做课件", params={}, subject=None, deps=deps, ctx=ctx
        )

        assert result.card_data["status"] == "invalid_params"
        assert result.card_data["retryable"] is True
        assert deps.celery_sent == []

    async def test_task_dedup_memory_claim_release(self) -> None:
        dedup = TaskDedup()
        assert await dedup.claim("k1") is True
        assert await dedup.claim("k1") is False  # 窗口内重复
        await dedup.release("k1")
        assert await dedup.claim("k1") is True   # 释放后可重试


class TestSubAgentWorkerTask:
    async def test_duplicate_completion_skipped(self) -> None:
        """幂等红线：complete:{task_id} 已存在 → 不执行、不写库。"""
        dedup = TaskDedup()
        await dedup.claim("complete:sa-x")
        result = await run_subagent_async(
            "sa-x", "oral_scoring_agent", "u1", dedup=dedup, deps=FakeDeps()
        )
        assert result["status"] == "duplicate"

    async def test_completion_persists_message_once(self) -> None:
        """worker 完成：跑子 Loop + 结果以 assistant 消息落库一次。"""
        gateway = ScriptedGateway(
            default_script=[GatewayEvent(text="评分完成", finish_reason="stop")]
        )
        deps = FakeDeps(gateway=gateway)
        session = FakeSession()
        dedup = TaskDedup()

        result = await run_subagent_async(
            "sa-1", "oral_scoring_agent", "u1", task="评分",
            session_id="s-1", session_factory=as_factory(session),
            deps=deps, dedup=dedup,
        )

        assert result["status"] == "completed"
        saved = [o for o in session.added if type(o).__name__ == "Message"]
        assert len(saved) == 1
        assert "评分完成" in saved[0].content
        assert saved[0].metadata_["task_id"] == "sa-1"
