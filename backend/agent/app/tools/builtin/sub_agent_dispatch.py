"""sub_agent_dispatch 工具：把任务派发给注册表中的子 Agent（子 Agent 即工具）。

- 教师 4 工具（ppt_generator/lesson_plan_generator/exam_paper_generator/
  learning_analysis）不实现为独立工具，仅以别名形式经本工具触达对应子 Agent
  （红线：它们 role_required="teacher" 的门槛在本工具内强制执行）；
- async_via=celery → celery_bridge.dispatch_async（受理卡片，立即返回）；
- async_via=none → runner.run_sync_subagent（进程内递归 Loop，单 ToolResult）；
- 深度红线：ctx.depth + 1 > 2 时结构化拒绝（禁止派生孙 Agent）。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from app.tools.base import EduToolBase, ToolContext, ToolResult

# 虚拟工具名/常用别名 → 子 Agent 注册名
AGENT_ALIASES: dict[str, str] = {
    "grading": "grading_agent",
    "ppt_generator": "ppt_agent",
    "lesson_plan_generator": "lesson_plan_agent",
    "exam_paper_generator": "exam_paper_agent",
    "learning_analysis": "learning_analysis_agent",
    "oral_scoring": "oral_scoring_agent",
}


class SubAgentDispatchArgs(BaseModel):
    agent: str = Field(min_length=1, description="子 Agent 名或别名（如 grading_agent / ppt_generator）")
    task: str = Field(default="", description="任务描述（必要上下文，禁止粘贴整段会话历史）")
    params: dict[str, Any] = Field(default_factory=dict, description="结构化表单参数")
    subject: Optional[str] = Field(default=None, description="学科（模型路由用，缺省取当前会话）")


class SubAgentDispatchTool(EduToolBase):
    """子 Agent 派发（写语义：可能建行/投递任务，串行执行）。"""

    name = "sub_agent_dispatch"
    description = "把复杂任务（批改/课件/教案/组卷/学情/口语评分）派发给专门的子 Agent 执行。"
    input_model = SubAgentDispatchArgs
    is_read_only = False
    is_concurrency_safe = False
    timeout_s = 45  # 同步子 Agent 默认 30s 上限 + 余量

    async def call(self, args: SubAgentDispatchArgs, ctx: ToolContext) -> ToolResult:
        from app.subagents import celery_bridge, runner
        from app.subagents.loader import load_registry

        registry = getattr(ctx.deps, "subagent_registry", None)
        if registry is None:
            registry = load_registry()  # 兜底：内置 YAML 基线（离线可加载）
        name = AGENT_ALIASES.get(args.agent, args.agent)
        definition = registry.maybe_get(name)
        if definition is None:
            return ToolResult(
                content=f"未注册的子 Agent：{args.agent}。可用：{', '.join(registry.names())}。"
            )
        if definition.role_required == "teacher" and not (
            getattr(ctx, "is_teacher", False) or getattr(ctx, "role", None) == "teacher"
        ):
            return ToolResult(
                content=f"「{definition.description}」仅教师身份可用。",
                card_data={"agent": name, "status": "forbidden", "retryable": False},
            )

        depth = int(getattr(ctx, "depth", 1)) + 1
        if depth > runner.MAX_SUBAGENT_DEPTH:
            return ToolResult(
                content=f"已达最大子 Agent 深度（{runner.MAX_SUBAGENT_DEPTH}），禁止继续派生。",
                card_data={"agent": name, "status": "depth_exceeded", "retryable": False},
            )

        subject = args.subject or getattr(ctx, "subject", None)
        if definition.async_via == "celery":
            return await celery_bridge.dispatch_async(
                definition, task=args.task, params=args.params,
                subject=subject, deps=ctx.deps, ctx=ctx,
            )
        return await runner.run_sync_subagent(
            definition, task=args.task, params=args.params,
            subject=subject, deps=ctx.deps, parent_ctx=ctx, depth=depth,
        )
