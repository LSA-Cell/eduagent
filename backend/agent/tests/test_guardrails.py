"""护栏决策链测试：fail-closed、角色校验、家长模式 ask、短路顺序（全部离线）。"""

from __future__ import annotations

import pytest

from app.guardrails.chain import GuardrailChain, QuotaPolicy
from app.loop.turn_config import TurnConfig

from tests.orchestration_fakes import FakeDeps, make_tool


def _config(**overrides) -> TurnConfig:
    base = {"user_id": "u1"}
    base.update(overrides)
    return TurnConfig(**base)


class TestModeration:
    async def test_moderation_outage_fail_closed(self) -> None:
        """审核服务异常 → deny（fail-closed），禁止 fail-open。"""
        deps = FakeDeps(moderate_result=RuntimeError("审核服务宕机"))
        decision = await deps.guardrails.check(make_tool("echo_tool"), _config(), {})
        assert decision.decision == "deny"
        assert decision.stage == "moderation"
        assert "fail-closed" in decision.reason

    async def test_moderation_unsafe_denies(self) -> None:
        deps = FakeDeps(moderate_result=False)
        decision = await deps.guardrails.check(make_tool("echo_tool"), _config(), {})
        assert (decision.decision, decision.stage) == ("deny", "moderation")


class TestRole:
    async def test_student_cannot_call_teacher_tool(self) -> None:
        deps = FakeDeps()
        tool = make_tool("lesson_plan_generator", role_required="teacher")
        decision = await deps.guardrails.check(tool, _config(role="student"), {})
        assert (decision.decision, decision.stage) == ("deny", "role")

    async def test_teacher_can_call_teacher_tool(self) -> None:
        deps = FakeDeps()
        tool = make_tool("lesson_plan_generator", role_required="teacher")
        decision = await deps.guardrails.check(
            tool, _config(role="teacher", is_teacher=True), {}
        )
        assert decision.decision == "allow"


class TestParental:
    async def test_minor_web_search_asks(self) -> None:
        """未成年人触达开放类工具 web_search → ask。"""
        deps = FakeDeps()
        tool = make_tool("web_search")
        decision = await deps.guardrails.check(tool, _config(is_minor=True), {})
        assert (decision.decision, decision.stage) == ("ask", "parental")

    async def test_minor_closed_tool_allows(self) -> None:
        deps = FakeDeps()
        tool = make_tool("calculator")
        decision = await deps.guardrails.check(tool, _config(is_minor=True), {})
        assert decision.decision == "allow"

    async def test_adult_web_search_allows(self) -> None:
        deps = FakeDeps()
        decision = await deps.guardrails.check(make_tool("web_search"), _config(), {})
        assert decision.decision == "allow"


class TestQuota:
    async def test_default_allow_all(self) -> None:
        deps = FakeDeps()
        decision = await deps.guardrails.check(make_tool("echo_tool"), _config(), {})
        assert decision.decision == "allow"

    async def test_configured_limit_denies_when_exhausted(self) -> None:
        deps = FakeDeps()
        chain = GuardrailChain(
            deps, quota_policy=QuotaPolicy(tier_limits={"free": {"echo_tool": 0}})
        )
        decision = await chain.check(make_tool("echo_tool"), _config(quota_tier="free"), {})
        assert (decision.decision, decision.stage) == ("deny", "quota")


class TestChainOrderAndAudit:
    async def test_short_circuit_order(self) -> None:
        """moderation 先于 role：审核宕机 + 越权工具 → 停在 moderation。"""
        deps = FakeDeps(moderate_result=RuntimeError("down"))
        tool = make_tool("teacher_tool", role_required="teacher")
        decision = await deps.guardrails.check(tool, _config(role="student"), {})
        assert decision.stage == "moderation"

    async def test_stage_order_fixed(self) -> None:
        deps = FakeDeps()
        assert deps.guardrails.stage_names == ["moderation", "role", "quota", "parental"]

    async def test_every_stage_decision_audited(self) -> None:
        deps = FakeDeps()
        tool = make_tool("echo_tool")
        await deps.guardrails.check(tool, _config(), {}, run_id="r1")
        stages = [d["stage"] for d in deps.audit.decisions]
        assert stages == ["moderation", "role", "quota", "parental"]
        assert all(d["decision"] == "allow" for d in deps.audit.decisions)
        assert all(d["run_id"] == "r1" for d in deps.audit.decisions)

    async def test_denied_decision_audited_with_reason(self) -> None:
        deps = FakeDeps()
        tool = make_tool("teacher_tool", role_required="teacher")
        await deps.guardrails.check(tool, _config(role="student"), {})
        last = deps.audit.decisions[-1]
        assert last["stage"] == "role" and last["decision"] == "deny" and last["reason"]
