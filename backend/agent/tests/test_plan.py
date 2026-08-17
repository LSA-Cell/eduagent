"""Plan Mode 两段式生成测试（增强机制 §2.1，全部离线）。

红线对应：
- 未 confirmed 投递 Celery 被拒（dispatch 前置状态校验）；
- 生成入参缺 outline_snapshot → Worker 拒收；
- confirm_token 跨用户/过期/跨 plan/一次性复用均被拒；
- 修改产生新 plan_version，旧版本保留不覆盖；
- 每次状态迁移触发回调（plan.status 事件序列）。
"""

from __future__ import annotations

import uuid

import pytest

from app.plan.dispatch import (
    PlanDispatchError,
    build_generation_payload,
    dispatch_plan_generation,
)
from app.plan.manager import (
    PlanError,
    PlanManager,
    PlanPermissionError,
    PlanTokenError,
)
from app.plan.models import PlanDocument
from app.plan.state_machine import PlanStateError, PlanStatus, ensure_transition
from app.tasks.plan_tasks import PlanTaskRejected, run_generate_from_plan
from tests.conftest import FakeSession, as_factory
from tests.orchestration_fakes import FakeDeps

from types import SimpleNamespace
from typing import Any

from app.loop.agent_loop import edu_agent_loop
from app.loop.deps_protocol import GatewayEvent
from app.plan.tool import SubmitPlanOutlineTool
from tests.orchestration_fakes import (
    ScriptedGateway,
    make_registry,
    make_tool,
    tool_call,
)

USER = str(uuid.uuid4())
OTHER_USER = str(uuid.uuid4())
SESSION_ID = str(uuid.uuid4())
OUTLINE = {
    "title": "一元一次方程单元卷",
    "sections": [{"name": "选择题", "count": 5}, {"name": "解答题", "count": 3}],
    "params": {"grade": "初一", "subject": "math", "count": 8},
}


async def _make_plan(session: FakeSession, *, transitions=None, token_ttl_s=None):
    manager = PlanManager(
        on_transition=transitions,
        **({"token_ttl_s": token_ttl_s} if token_ttl_s is not None else {}),
    )
    plan, token = await manager.draft_and_submit(
        session, user_id=USER, session_id=SESSION_ID,
        skill_code="exam_paper", outline=OUTLINE,
    )
    return manager, plan, token


class TestStateMachine:
    def test_legal_and_illegal_transitions(self) -> None:
        ensure_transition(PlanStatus.DRAFTING, PlanStatus.AWAITING_CONFIRM)
        ensure_transition(PlanStatus.AWAITING_CONFIRM, PlanStatus.CONFIRMED)
        ensure_transition(PlanStatus.CONFIRMED, PlanStatus.GENERATING)
        ensure_transition(PlanStatus.GENERATING, PlanStatus.COMPLETED)
        with pytest.raises(PlanStateError):
            ensure_transition(PlanStatus.DRAFTING, PlanStatus.GENERATING)  # 跳过确认
        with pytest.raises(PlanStateError):
            ensure_transition(PlanStatus.COMPLETED, PlanStatus.CANCELLED)  # 终态
        with pytest.raises(PlanStateError):
            ensure_transition(PlanStatus.AWAITING_CONFIRM, PlanStatus.COMPLETED)


class TestDraftAndConfirm:
    async def test_skill_whitelist_enforced(self) -> None:
        """白名单外 skill（快问快答类）禁止进入 Plan 流程。"""
        session = FakeSession()
        manager = PlanManager()
        with pytest.raises(PlanError, match="白名单"):
            await manager.draft_and_submit(
                session, user_id=USER, session_id=SESSION_ID,
                skill_code="photo_solve", outline=OUTLINE,
            )

    async def test_transition_event_sequence(self) -> None:
        """每次状态迁移触发回调（Loop 侧据此推 plan.status 事件）。"""
        transitions: list[tuple[str, str]] = []
        session = FakeSession()

        def on_transition(plan, old, new):
            transitions.append((old.value, new.value))

        manager, plan, token = await _make_plan(session, transitions=on_transition)
        assert transitions == [("drafting", "awaiting_confirm")]
        assert plan.status == "awaiting_confirm"
        assert plan.confirm_token_hash and len(plan.confirm_token_hash) == 64  # 只存哈希
        assert plan.confirm_expires_at is not None

        await manager.confirm(session, str(plan.id), user_id=USER, token=token)
        assert transitions[-1] == ("awaiting_confirm", "confirmed")
        assert plan.status == "confirmed" and plan.confirmed_at is not None
        assert plan.confirm_token_hash is None  # 一次性：确认后令牌失效

    async def test_confirm_token_cross_user_rejected(self) -> None:
        session = FakeSession()
        manager, plan, token = await _make_plan(session)
        with pytest.raises(PlanPermissionError):
            await manager.confirm(session, str(plan.id), user_id=OTHER_USER, token=token)

    async def test_confirm_token_expired_rejected(self) -> None:
        """过期令牌一律拒绝，plan 迁移到 expired。"""
        session = FakeSession()
        manager, plan, token = await _make_plan(session, token_ttl_s=-1)  # 立即过期
        with pytest.raises(PlanTokenError, match="过期|超时"):
            await manager.confirm(session, str(plan.id), user_id=USER, token=token)
        assert plan.status == PlanStatus.EXPIRED.value
        assert plan.confirm_token_hash is None  # 过期令牌作废

    async def test_confirm_token_cross_plan_rejected(self) -> None:
        """plan A 的令牌不能用于 plan B（哈希绑定 plan 行）。"""
        session = FakeSession()
        manager_a, plan_a, token_a = await _make_plan(session)
        _, plan_b, _ = await _make_plan(session)
        with pytest.raises(PlanTokenError):
            await manager_a.confirm(session, str(plan_b.id), user_id=USER, token=token_a)

    async def test_confirm_token_reuse_rejected(self) -> None:
        """令牌一次性：第二次确认（状态已 confirmed）被拒。"""
        session = FakeSession()
        manager, plan, token = await _make_plan(session)
        await manager.confirm(session, str(plan.id), user_id=USER, token=token)
        with pytest.raises(PlanStateError):
            await manager.confirm(session, str(plan.id), user_id=USER, token=token)


class TestRevise:
    async def test_revise_creates_new_version_and_keeps_old(self) -> None:
        session = FakeSession()
        manager, old_plan, old_token = await _make_plan(session)
        new_outline = {**OUTLINE, "title": "一元一次方程单元卷（修订）"}

        new_plan, new_token = await manager.revise(
            session, str(old_plan.id), user_id=USER, outline=new_outline
        )

        assert new_plan.id != old_plan.id
        assert new_plan.version == old_plan.version + 1
        assert new_plan.parent_plan_id == old_plan.id  # 新版本指回旧版本
        assert new_plan.outline["title"].endswith("（修订）")
        # 旧版本保留（不覆盖），状态 cancelled、令牌作废
        assert old_plan.status == PlanStatus.CANCELLED.value
        assert old_plan.outline["title"] == "一元一次方程单元卷"
        plans = [o for o in session.added if isinstance(o, PlanDocument)]
        assert len(plans) == 2
        with pytest.raises(PlanStateError):
            await manager.confirm(session, str(old_plan.id), user_id=USER, token=old_token)


class TestDispatch:
    async def test_unconfirmed_dispatch_rejected(self) -> None:
        """红线：大纲未 confirmed 禁止投递 Celery 生成任务。"""
        session = FakeSession()
        _, plan, _ = await _make_plan(session)  # awaiting_confirm
        deps = FakeDeps()
        with pytest.raises(PlanDispatchError, match="未 confirmed"):
            dispatch_plan_generation(deps, plan)
        assert deps.celery_sent == []

    async def test_confirmed_dispatch_carries_outline_snapshot(self) -> None:
        session = FakeSession()
        manager, plan, token = await _make_plan(session)
        await manager.confirm(session, str(plan.id), user_id=USER, token=token)
        deps = FakeDeps()

        payload = dispatch_plan_generation(deps, plan, record_id="rec-1")

        assert deps.celery_sent == [("agent.plan.generate", payload)]
        assert payload["outline_snapshot"] == OUTLINE  # 同源约束：确认时快照
        assert payload["plan_id"] == str(plan.id)
        assert payload["record_id"] == "rec-1"
        # 快照为深拷贝：投递后大纲再改不影响已投递任务
        plan.outline["title"] = "被篡改"
        assert payload["outline_snapshot"]["title"] == "一元一次方程单元卷"

    def test_empty_snapshot_rejected(self) -> None:
        plan = PlanDocument(skill_code="ppt", version=1, outline={}, status="confirmed")
        with pytest.raises(PlanDispatchError, match="快照为空"):
            build_generation_payload(plan)


class TestWorker:
    async def test_missing_outline_snapshot_rejected(self) -> None:
        """红线：生成任务入参缺 outline_snapshot，Worker 必须拒收。"""
        with pytest.raises(PlanTaskRejected, match="outline_snapshot"):
            await run_generate_from_plan(
                str(uuid.uuid4()), outline_snapshot=None,
                session_factory=as_factory(FakeSession()),
            )

    async def test_unconfirmed_plan_rejected_by_worker(self) -> None:
        """Worker 侧复核状态：非 confirmed 拒收（防绕过投递端校验）。"""
        session = FakeSession()
        _, plan, _ = await _make_plan(session)  # awaiting_confirm
        with pytest.raises(PlanTaskRejected, match="非 confirmed"):
            await run_generate_from_plan(
                str(plan.id), outline_snapshot=OUTLINE,
                session_factory=as_factory(session),
            )

    async def test_happy_path_generating_to_completed(self) -> None:
        session = FakeSession()
        manager, plan, token = await _make_plan(session)
        await manager.confirm(session, str(plan.id), user_id=USER, token=token)

        result = await run_generate_from_plan(
            str(plan.id), outline_snapshot=OUTLINE,
            session_factory=as_factory(session),
        )

        assert result["status"] == "completed"
        assert plan.status == PlanStatus.COMPLETED.value  # confirmed→generating→completed


class TestPlanToolInLoop:
    """submit_plan_outline 在 Loop 中的事件序列与 plan_mode 只读约束。"""

    def _req(self, **overrides: Any) -> SimpleNamespace:
        base = dict(
            user_id=USER, message="帮我组一份卷", session_id=SESSION_ID,
            subject="math", model=None, skill_code="exam_paper",
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    async def test_outline_events_and_card(self) -> None:
        """大纲提交 → plan.status(awaiting_confirm) + plan.outline + plan_outline 卡片。"""
        session = FakeSession()
        gateway = ScriptedGateway(stream_scripts=[
            [GatewayEvent(
                tool_calls=[tool_call(
                    "submit_plan_outline",
                    {"skill_code": "exam_paper", "outline": OUTLINE},
                )],
                finish_reason="tool_calls",
            )],
            [GatewayEvent(text="大纲已生成，请确认。", finish_reason="stop")],
        ])
        deps = FakeDeps(
            gateway=gateway, registry=make_registry(SubmitPlanOutlineTool()),
            session=session,
        )

        events = [e async for e in edu_agent_loop(self._req(), deps)]

        types = [e.event for e in events]
        assert "plan.status" in types
        assert events[types.index("plan.status")].data["status"] == "awaiting_confirm"
        outline_events = [e for e in events if e.event == "plan.outline"]
        assert len(outline_events) == 1
        assert outline_events[0].data["version"] == 1
        assert outline_events[0].data["outline"] == OUTLINE
        cards = [e for e in events if e.event == "card"]
        assert cards and cards[0].data["card_type"] == "plan_outline"
        assert cards[0].data["card_data"]["confirm_token"]  # 客户端确认回传用
        plans = [o for o in session.added if isinstance(o, PlanDocument)]
        assert plans and plans[0].status == "awaiting_confirm"

    async def test_plan_mode_blocks_write_tools(self) -> None:
        """drafting/awaiting_confirm 期间写工具被拒（只读工具与提交大纲放行）。"""
        session = FakeSession()
        write_tool = make_tool("write_tool", is_read_only=False)
        gateway = ScriptedGateway(stream_scripts=[
            [GatewayEvent(
                tool_calls=[tool_call("write_tool", {"text": "直接生成全文"})],
                finish_reason="tool_calls",
            )],
            [GatewayEvent(text="好的，我先出大纲。", finish_reason="stop")],
        ])
        deps = FakeDeps(
            gateway=gateway,
            registry=make_registry(SubmitPlanOutlineTool(), write_tool),
            session=session,
        )

        await self._collect_events(self._req(), deps)

        tool_msgs = [
            m for m in gateway.stream_calls[1]["messages"] if m.get("role") == "tool"
        ]
        assert tool_msgs and "大纲尚未确认" in tool_msgs[0]["content"]

    async def _collect_events(self, req: Any, deps: Any) -> list[Any]:
        return [e async for e in edu_agent_loop(req, deps)]
