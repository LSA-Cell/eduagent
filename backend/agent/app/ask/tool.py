"""内置工具 ask_user_question：Agent 中途用选项卡片澄清需求（增强机制 §2.2）。

协议与红线落实：
- 每次 ≤3 题；每题 options 2–6 个，label ≤20 字，multi_select 默认 false；
  preview 仅产物类选项（大纲/模板风格/卷面样式）使用；
- 推荐项约定：label 以"（推荐）"结尾的选项必须放第一位，否则拒绝并回注
  （可选项化的追问禁止退化为自由文本——由本工具的强协议保证）；
- 频次：每 run ≤2 次；禁止连续两个 turn 都 ask（第三次/连续追问回注拒绝）；
- 挂起闭环：本工具登记挂起并推 `ask` 事件；Loop 见 pending_ask 标记后挂起
  等答复/超时（app/ask/policy.py 超时分级），挂起期间无模型调用。

副作用声明：仅在注入的 ask_store 登记挂起记录（TTL 120s），不写业务表。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from app.ask.store import ASK_TTL_S
from app.tools.base import EduToolBase, ToolContext, ToolResult

MAX_QUESTIONS_PER_ASK = 3
MAX_ASKS_PER_RUN = 2  # 每 run ask ≤2 次（第 3 次必须合并或放弃追问）
RECOMMENDED_SUFFIX = "（推荐）"


class AskOption(BaseModel):
    label: str = Field(min_length=1, max_length=20)
    description: str = Field(default="")
    preview: Optional[dict[str, Any]] = Field(
        default=None, description="产物类选项的结构化预览（大纲/模板风格/卷面样式）"
    )


class AskQuestion(BaseModel):
    question: str = Field(min_length=1)
    options: list[AskOption] = Field(min_length=2, max_length=6)
    multi_select: bool = False
    sensitive: bool = Field(
        default=False,
        description="敏感决策（付费/删除/公开发布/家长确认）置 true：超时按取消处理",
    )


class AskUserQuestionArgs(BaseModel):
    questions: list[AskQuestion] = Field(min_length=1, max_length=MAX_QUESTIONS_PER_ASK)

    @field_validator("questions")
    @classmethod
    def _recommended_first(cls, questions: list[AskQuestion]) -> list[AskQuestion]:
        """推荐项约定：带"（推荐）"后缀的选项必须位于首位。"""
        for q in questions:
            for index, opt in enumerate(q.options):
                if opt.label.endswith(RECOMMENDED_SUFFIX) and index != 0:
                    raise ValueError(
                        f"问题「{q.question}」的推荐项必须放第一位"
                        f"（label 以{RECOMMENDED_SUFFIX}结尾且 index=0）"
                    )
        return questions


class AskUserQuestionTool(EduToolBase):
    """结构化中途提问（读语义：不改业务数据；挂起闭环由 Loop 完成）。"""

    name = "ask_user_question"
    description = (
        "中途向用户发起结构化选项提问以澄清需求（每次最多 3 题，每题 2-6 个选项，"
        "label≤20 字）。能选项化的追问必须用本工具，禁止退化为自由文本提问。"
        f"有推荐答案时该选项放第一位且 label 末尾追加{RECOMMENDED_SUFFIX}。"
        "敏感决策（付费/删除/公开发布/家长确认）必须将 sensitive 置 true（超时按取消）。"
        "每次运行最多提问 2 次，且禁止连续两轮都提问。"
    )
    input_model = AskUserQuestionArgs
    is_read_only = True  # 仅登记挂起记录；无副作用写
    is_concurrency_safe = False  # 挂起语义，禁止与其他工具并发批
    timeout_s = 10

    async def call(self, args: AskUserQuestionArgs, ctx: ToolContext) -> ToolResult:
        state = getattr(ctx, "turn_state", None)
        if state is None:
            return ToolResult(content="当前上下文不支持中途提问（缺少运行状态）。")
        # ---- 频次红线：每 run ≤2 次；禁止连续两 turn 都 ask -----------------
        if int(getattr(state, "ask_count", 0)) >= MAX_ASKS_PER_RUN:
            return ToolResult(
                content=f"本轮对话已提问 {MAX_ASKS_PER_RUN} 次（上限），"
                "请把剩余问题合并进已有提问，或直接按合理默认作答。"
            )
        ask_turns = list(getattr(state, "ask_turns", []))
        if ask_turns and ask_turns[-1] == int(getattr(state, "turn_count", 0)) - 1:
            return ToolResult(
                content="上一轮已发起过提问，禁止连续追问；请合并问题或直接按合理默认作答。"
            )
        store = getattr(ctx.deps, "ask_store", None) if ctx.deps is not None else None
        if store is None:
            return ToolResult(content="提问通道不可用（缺少挂起存储），请直接作答。")

        questions = [q.model_dump() for q in args.questions]
        ask_id = await store.register(
            user_id=ctx.user_id, run_id=ctx.run_id, questions=questions, ttl_s=ASK_TTL_S
        )
        # 提问即消耗额度（防止模型用失败重试绕过频次上限）
        state.ask_count = int(getattr(state, "ask_count", 0)) + 1
        ask_turns.append(int(getattr(state, "turn_count", 0)))
        state.ask_turns = ask_turns

        sink = getattr(ctx, "event_sink", None)
        if sink is not None:
            sink("ask", {"ask_id": ask_id, "questions": questions})
        return ToolResult(
            content=f"已向用户发起结构化提问（ask_id={ask_id}），等待答复或超时。",
            pending_ask={
                "ask_id": ask_id,
                "questions": questions,
                "sensitive": any(q.get("sensitive") for q in questions),
            },
        )
