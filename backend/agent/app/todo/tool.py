"""内置工具 todo_write：多阶段任务的可见任务清单（增强机制 §2.4）。

红线落实：
- 仅允许在多阶段子 Agent 任务中使用（ctx.depth ≥ 2 或 skill 属于组卷/学情/
  PPT/教案/学习计划/DreamTask 集合）；普通问答调用直接拒绝；
- 同一时间只允许 1 个 in_progress（入参校验 + 与旧快照合并校验双保险）；
- 禁止跳状态：pending 直接 completed 会被拒绝（须先经过 in_progress；
  in_progress → completed 要求期间确有其他工具执行记录佐证）；
- 每次更新推 card 事件（card_type=todo_list）；快照由 Loop 随 agent_runs 落库；
- Todo 只作进度可视化与自约束，不写任何业务表。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.tools.base import EduToolBase, ToolContext, ToolResult

# 允许使用 todo 的多阶段任务上下文（skill_code 或 depth≥2 的子 Agent）
TODO_ALLOWED_SKILLS: frozenset[str] = frozenset(
    {"ppt", "lesson_plan", "exam_paper", "study_plan", "analysis", "dream"}
)

# 最终交付卡片类型（全部 completed 前禁止产出，Loop 侧 card_gate 强制）
FINAL_DELIVERY_CARD_TYPES: frozenset[str] = frozenset(
    {"ppt_preview", "lesson_plan_preview", "exam_preview", "plan_card", "analysis_report"}
)

TodoStatus = Literal["pending", "in_progress", "completed"]


class TodoItem(BaseModel):
    content: str = Field(min_length=1, max_length=200)
    status: TodoStatus = "pending"


class TodoWriteArgs(BaseModel):
    """完整替换式更新（每次提交全量清单）。"""

    items: list[TodoItem] = Field(min_length=1, max_length=20)

    @field_validator("items")
    @classmethod
    def _single_in_progress(cls, items: list[TodoItem]) -> list[TodoItem]:
        in_progress = [i for i in items if i.status == "in_progress"]
        if len(in_progress) > 1:
            raise ValueError("同一时间只允许 1 个 in_progress 待办")
        return items


def validate_transition(
    old_items: list[dict[str, Any]],
    new_items: list[TodoItem],
    *,
    tools_executed_since_update: int,
) -> Optional[str]:
    """跳状态校验；返回拒绝原因或 None。

    - pending → completed：禁止（须先经过 in_progress）；
    - in_progress → completed：要求期间确有工具执行记录佐证。
    """
    old_by_content = {str(i.get("content")): str(i.get("status")) for i in old_items}
    for item in new_items:
        old_status = old_by_content.get(item.content)
        if old_status == "pending" and item.status == "completed":
            return f"待办「{item.content}」禁止从 pending 直接跳 completed（须先进行中）"
        if (
            old_status == "in_progress"
            and item.status == "completed"
            and tools_executed_since_update <= 0
        ):
            return f"待办「{item.content}」标记完成前缺少对应工具执行记录佐证"
    return None


class TodoWriteTool(EduToolBase):
    """可见任务清单（写语义仅限运行内状态；不写业务表，串行执行）。"""

    name = "todo_write"
    description = (
        "多阶段任务（组卷/学情分析/PPT 生成/教案/学习计划）的进度清单：每次提交全量 "
        "items[{content, status: pending|in_progress|completed}]，同一时间仅允许 1 个 "
        "in_progress。仅多阶段任务可用；完成一个阶段再更新。清单只是进度可视化，"
        "不是业务数据存储。"
    )
    input_model = TodoWriteArgs
    is_read_only = True  # 不写业务表（仅运行内状态 + 卡片）
    is_concurrency_safe = False
    card_type = "todo_list"
    timeout_s = 5

    async def call(self, args: TodoWriteArgs, ctx: ToolContext) -> ToolResult:
        skill_code = getattr(ctx, "skill_code", None)
        depth = int(getattr(ctx, "depth", 1) or 1)
        if depth < 2 and skill_code not in TODO_ALLOWED_SKILLS:
            return ToolResult(
                content="todo_write 仅在多阶段任务（组卷/学情分析/PPT/教案/学习计划）中可用，"
                "当前任务无需清单，请直接执行。"
            )
        state = getattr(ctx, "turn_state", None)
        if state is None:
            return ToolResult(content="当前上下文不支持任务清单（缺少运行状态）。")

        old_items = list(getattr(state, "todo_items", []))
        tools_since = int(getattr(state, "tool_calls_total", 0)) - int(
            getattr(state, "todo_marker_tool_calls", 0)
        )
        reason = validate_transition(
            old_items, args.items, tools_executed_since_update=tools_since
        )
        if reason is not None:
            return ToolResult(content=f"待办更新被拒：{reason}。")

        items = [i.model_dump() for i in args.items]
        state.todo_items = items
        state.todo_marker_tool_calls = int(getattr(state, "tool_calls_total", 0))
        done = sum(1 for i in items if i["status"] == "completed")
        return ToolResult(
            content=f"任务清单已更新：共 {len(items)} 项，已完成 {done} 项。",
            card_type="todo_list",
            card_data={"items": items},
        )
