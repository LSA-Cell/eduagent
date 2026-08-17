"""内置工具 submit_plan_outline：Plan Mode 大纲阶段出口（增强机制 §2.1）。

模型在白名单 skill 上下文中先产出大纲 JSON 并调用本工具：
  1. PlanManager 建行（drafting → awaiting_confirm），签发一次性 confirm_token；
  2. 推送 plan.status（每次迁移）与 plan.outline 事件；大纲卡片走既有 card 事件
     （card_type=plan_outline，card_data 携带 confirm_token 供客户端确认回传）；
  3. 大纲未经 confirmed，模型不得自行开始生成全文（生成任务由确认端点投递）。

本工具是 plan_mode（drafting/awaiting_confirm 期间仅只读工具）的唯一写例外
（执行器按 PLAN_MODE_WRITE_EXEMPTIONS 放行）。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from app.plan.manager import (
    CONFIRM_TOKEN_TTL_S,
    PLAN_SKILL_WHITELIST,
    PlanError,
    PlanManager,
)
from app.plan.state_machine import PlanStatus
from app.tools.base import EduToolBase, ToolContext, ToolResult

# plan_mode 下允许的唯一写工具（其余写工具在 drafting/awaiting_confirm 期间被拒）
PLAN_MODE_WRITE_EXEMPTIONS: frozenset[str] = frozenset({"submit_plan_outline"})


class PlanOutlineArgs(BaseModel):
    skill_code: str = Field(
        min_length=1,
        description=f"长内容生成技能编码，白名单：{sorted(PLAN_SKILL_WHITELIST)}",
    )
    outline: dict[str, Any] = Field(
        description="大纲 JSON（如试卷：题型分布/知识点覆盖/难度配比/逐题标题；PPT：每页标题+要点）"
    )


class SubmitPlanOutlineTool(EduToolBase):
    """提交大纲进入待确认（写语义：建 plan_documents 行，串行执行）。"""

    name = "submit_plan_outline"
    description = (
        "两段式生成第一步：把长内容（PPT/教案/试卷/学习计划/学情分析）的大纲提交给用户确认。"
        "仅在对应技能上下文中可用；提交后必须等待用户确认，禁止自行开始生成全文。"
    )
    input_model = PlanOutlineArgs
    is_read_only = False  # 写 plan_documents；plan_mode 白名单例外放行
    is_concurrency_safe = False
    timeout_s = 15

    async def call(self, args: PlanOutlineArgs, ctx: ToolContext) -> ToolResult:
        if args.skill_code not in PLAN_SKILL_WHITELIST:
            return ToolResult(
                content=(
                    f"skill {args.skill_code!r} 不支持两段式大纲确认"
                    f"（白名单：{sorted(PLAN_SKILL_WHITELIST)}）；请直接作答。"
                )
            )
        if ctx.deps is None:
            return ToolResult(content="当前上下文缺少依赖注入，无法创建大纲。")

        def on_transition(plan: Any, old: Any, new: Any) -> None:
            sink = getattr(ctx, "event_sink", None)
            if sink is not None:
                sink("plan.status", {"plan_id": str(plan.id), "status": new.value})

        manager = PlanManager(on_transition=on_transition)
        try:
            async with ctx.deps.session_factory() as session:
                plan, token = await manager.draft_and_submit(
                    session,
                    user_id=ctx.user_id,
                    session_id=ctx.session_id,
                    skill_code=args.skill_code,
                    outline=args.outline,
                )
                await session.commit()
        except PlanError as exc:
            return ToolResult(content=f"大纲提交失败：{exc}")
        except Exception as exc:  # noqa: BLE001 - 结构化回注，禁止冒泡中断 Loop
            return ToolResult(content=f"大纲提交失败：{type(exc).__name__}（可重试）。")

        sink = getattr(ctx, "event_sink", None)
        if sink is not None:
            sink("plan.outline", {
                "plan_id": str(plan.id),
                "version": int(plan.version),
                "outline": args.outline,
            })
        return ToolResult(
            content=(
                f"大纲已生成并提交用户确认（plan_id={plan.id}，version={plan.version}）。"
                "请用一句话请用户确认大纲：确认后开始生成全文；如需修改请用户提出意见。"
                "在大纲被确认前，禁止调用任何生成类/写类工具。"
            ),
            card_type="plan_outline",
            card_data={
                "plan_id": str(plan.id),
                "version": int(plan.version),
                "skill_code": args.skill_code,
                "outline": args.outline,
                "status": PlanStatus.AWAITING_CONFIRM.value,
                "confirm_token": token,
                "expires_in_s": CONFIRM_TOKEN_TTL_S,
            },
        )
