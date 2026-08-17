"""Stop Hooks 收尾校验测试（增强机制 §2.5，全部离线）。

红线对应：
- 错误数学答案被 math_verify 拦截 → blocking 消息注入强制继续 →
  第 2 次仍失败 → 第 3 次带 warning 标记交付并落 stop_hook_audits；
- 强制继续上限 2 次（第 3 次不再注入）；
- 校验器超时按失败处理（wait_for ≤2s，测试用 0.2s 注入）；
- 校验器只读上下文（StopHookContext 无 session/deps，结构性禁止写业务表）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.loop.agent_loop import edu_agent_loop
from app.loop.deps_protocol import GatewayEvent
from app.stop_hooks.base import StopHookContext, StopHookResult
from app.stop_hooks.builtins import (
    CompletenessCheckHook,
    GradingConsistencyHook,
    MathVerifyHook,
)
from app.stop_hooks.service import HookRegistry, default_registry, run_matched_hooks

from tests.orchestration_fakes import FakeDeps, ScriptedGateway, make_registry


def _req(**overrides: Any) -> SimpleNamespace:
    base = dict(
        user_id="u1", message="解方程 2x+3=7", session_id=None,
        subject="math", model=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def _collect(req: Any, deps: Any) -> list[Any]:
    return [event async for event in edu_agent_loop(req, deps)]


def _text_script(text: str) -> list[GatewayEvent]:
    return [GatewayEvent(text=text, finish_reason="stop")]


class TestMathVerify:
    async def test_wrong_solution_rejected(self) -> None:
        """方程解回代不成立 → 拦截（2x+3=7 的正确解是 x=2，不是 x=5）。"""
        hook = MathVerifyHook()
        ctx = StopHookContext(answer_text="解得 x = 5", user_text="解方程 2x+3=7")
        assert hook.applies(ctx)
        result = await hook.validate(ctx)
        assert result.passed is False
        assert "回代不成立" in result.reason

    async def test_correct_solution_passes(self) -> None:
        hook = MathVerifyHook()
        ctx = StopHookContext(answer_text="解得 x = 2", user_text="解方程 2x+3=7")
        result = await hook.validate(ctx)
        assert result.passed is True

    async def test_no_equation_in_question_passes(self) -> None:
        """题面无方程可复核时不误伤。"""
        hook = MathVerifyHook()
        ctx = StopHookContext(answer_text="面积是 12 平方厘米", user_text="求这个三角形的面积")
        assert hook.applies(ctx) is False  # 无 "变量=数值" 声明
        result = await hook.validate(ctx)
        assert result.passed is True


class TestGradingConsistency:
    async def test_missing_verdict_or_rubric_rejected(self) -> None:
        hook = GradingConsistencyHook()
        ctx = StopHookContext(
            answer_text="批改完成",
            cards=({"card_type": "grading", "card_data": {"results": [
                {"verdict": "correct"},  # 缺标准答案/评分细则引用
                {"explanation": "见课本"},  # 缺 verdict
            ]}},),
        )
        assert hook.applies(ctx)
        result = await hook.validate(ctx)
        assert result.passed is False
        assert "一致性" in result.reason

    async def test_consistent_grading_passes(self) -> None:
        hook = GradingConsistencyHook()
        ctx = StopHookContext(
            answer_text="批改完成",
            cards=({"card_type": "grading", "card_data": {"results": [
                {"verdict": "wrong", "standard_answer": "x=2", "explanation": "移项错误"},
            ]}},),
        )
        result = await hook.validate(ctx)
        assert result.passed is True


class TestCompletenessCheck:
    async def test_lesson_plan_missing_modules_rejected(self) -> None:
        hook = CompletenessCheckHook()
        ctx = StopHookContext(
            answer_text="教案：教学目标：掌握一元一次方程。教学过程：讲授与练习。",
            skill_code="lesson_plan",
        )
        assert hook.applies(ctx)
        result = await hook.validate(ctx)
        assert result.passed is False
        assert set(result.detail["missing"]) == {"重难点", "板书", "作业", "教学反思"}

    async def test_ppt_structure_via_card(self) -> None:
        hook = CompletenessCheckHook()
        ctx = StopHookContext(
            answer_text="PPT 已生成",
            cards=({"card_type": "ppt_preview", "card_data": {
                "title": "课件标题页", "outline": "目录",
                "slides": ["正文1", "正文2"], "summary": "总结页",
            }},),
        )
        result = await hook.validate(ctx)
        assert result.passed is True


class SlowHook:
    """永远超时的校验器（sleep 超过注入的超时阈值）。"""

    name = "slow_hook"

    def applies(self, ctx: StopHookContext) -> bool:
        return True

    async def validate(self, ctx: StopHookContext) -> StopHookResult:
        await asyncio.sleep(5.0)
        return StopHookResult(passed=True)


class TestLoopIntegration:
    async def test_wrong_math_answer_forced_continue_then_warning(self) -> None:
        """错误答案 → 强制继续 ×2 → 第 3 次失败带 warning 交付并落审计。"""
        gateway = ScriptedGateway(stream_scripts=[
            _text_script("解得 x = 5"),  # 第 1 次：错
            _text_script("验算后 x = 5"),  # 第 2 次：仍错
            _text_script("确认 x = 5"),  # 第 3 次：还错
        ])
        deps = FakeDeps(gateway=gateway, registry=make_registry())
        deps.stop_hooks = default_registry()

        events = await _collect(_req(), deps)

        assert len(gateway.stream_calls) == 3  # 强制继续 2 次后第 3 次交付
        # blocking 注入：后续模型请求历史中含失败原因
        second_messages = gateway.stream_calls[1]["messages"]
        assert any(
            "质量校验未通过" in str(m.get("content")) and m.get("role") == "user"
            for m in second_messages
        )
        # 审计：3 行 math_verify 全失败，强制继续序号 1/2/3
        hook_audits = [a for a in deps.audit.stop_hooks if a["hook_name"] == "math_verify"]
        assert len(hook_audits) == 3
        assert [a["passed"] for a in hook_audits] == [False, False, False]
        assert [a["forced_continue_count"] for a in hook_audits] == [1, 2, 3]
        # 第 3 次带 warning 标记交付（前端可见标识，禁止静默降级）
        warnings = [
            e for e in events
            if e.event == "card" and e.data.get("card_type") == "quality_warning"
        ]
        assert len(warnings) == 1
        assert warnings[0].data["card_data"]["hooks"] == ["math_verify"]
        assert events[-1].data["finish_reason"] == "stop"

    async def test_validator_timeout_counts_as_failure(self) -> None:
        """校验器超时按失败处理（单校验器 wait_for 强制 ≤ 阈值）。"""
        gateway = ScriptedGateway(stream_scripts=[
            _text_script("普通回答一"), _text_script("普通回答二"), _text_script("普通回答三"),
        ])
        deps = FakeDeps(gateway=gateway, registry=make_registry())
        deps.stop_hooks = HookRegistry([SlowHook()])
        deps.stop_hook_timeout_s = 0.2  # 注入短阈值，SlowHook 每次必超时

        events = await _collect(_req(message="随便聊聊"), deps)

        assert len(gateway.stream_calls) == 3  # 超时 → 失败 → 强制继续 ×2 → warning 交付
        hook_audits = [a for a in deps.audit.stop_hooks if a["hook_name"] == "slow_hook"]
        assert len(hook_audits) == 3
        assert all(a["passed"] is False for a in hook_audits)
        assert all(a["detail"].get("error") == "timeout" for a in hook_audits)
        assert any(
            e.event == "card" and e.data.get("card_type") == "quality_warning"
            for e in events
        )

    async def test_completeness_forces_fix_then_passes(self) -> None:
        """教案缺模块 → 强制继续 → 补全后通过，无 warning。"""
        gateway = ScriptedGateway(stream_scripts=[
            _text_script("教案：教学目标：掌握方程解法。教学过程：讲练结合。"),
            _text_script(
                "教案：教学目标：掌握方程解法。重难点：移项。教学过程：讲练结合。"
                "板书设计：步骤示例。作业布置：课后 1-5 题。教学反思：待定。"
            ),
        ])
        deps = FakeDeps(gateway=gateway, registry=make_registry())
        deps.stop_hooks = HookRegistry([CompletenessCheckHook()])

        events = await _collect(_req(message="写一份教案", skill_code="lesson_plan"), deps)

        assert len(gateway.stream_calls) == 2
        hook_audits = deps.audit.stop_hooks
        assert [a["passed"] for a in hook_audits] == [False, True]
        assert not any(
            e.event == "card" and e.data.get("card_type") == "quality_warning"
            for e in events
        )
        assert events[-1].data["finish_reason"] == "stop"

    async def test_hook_context_is_read_only(self) -> None:
        """红线：校验器上下文不携带 session/deps（结构性禁止写业务表）。"""
        ctx = StopHookContext(answer_text="x = 2", user_text="解方程 2x+3=7")
        assert not hasattr(ctx, "session") and not hasattr(ctx, "deps")
