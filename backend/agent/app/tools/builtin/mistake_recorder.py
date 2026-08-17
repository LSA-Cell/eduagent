"""mistake_recorder 工具：把一道错题写入用户错题本（写工具）。

副作用声明：经 ctx.deps.session_factory 写 mistake_records 表（并提交事务）；
逻辑复用 app.services.mistake_service.record_mistake（艾宾浩斯初始化）。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.tools.base import EduToolBase, ToolContext, ToolResult


class MistakeRecorderArgs(BaseModel):
    """错题录入参数（user_answer → ORM wrong_answer 的映射在 service 层完成）。"""

    question: str = Field(min_length=1, description="题目文本")
    user_answer: str = Field(default="", description="学生的错误作答")
    correct_answer: str = Field(default="", description="正确答案")
    analysis: str = Field(default="", description="错因解析")
    knowledge_tags: list[str] = Field(default_factory=list, description="知识点标签")
    subject: Optional[str] = Field(default=None, description="学科（缺省取当前会话学科）")
    source_message_id: Optional[str] = None


class MistakeRecorderTool(EduToolBase):
    """错题记录（写）：role_required=None，全体身份可用。"""

    name = "mistake_recorder"
    description = "把一道错题记录到用户错题本（含正确答案/解析/知识点），自动安排艾宾浩斯复习。"
    input_model = MistakeRecorderArgs
    is_read_only = False  # 写工具：串行执行
    is_concurrency_safe = False
    card_type = "mistake_card"
    timeout_s = 10

    async def call(self, args: MistakeRecorderArgs, ctx: ToolContext) -> ToolResult:
        from app.services import mistake_service

        subject = args.subject or getattr(ctx, "subject", None)
        async with ctx.deps.session_factory() as session:
            record = await mistake_service.record_mistake(
                session,
                ctx.user_id,
                data=args.model_dump(),
                subject=subject,
                source_message_id=args.source_message_id,
            )
            await session.commit()
        record_id = str(getattr(record, "id", ""))
        next_review = getattr(record, "next_review_at", None)
        return ToolResult(
            content=(
                f"已记录错题（id={record_id}）。"
                f"下次复习时间：{next_review.isoformat() if next_review else '按计划'}。"
            ),
            card_type=self.card_type,
            card_data={
                "mistake_id": record_id,
                "subject": subject,
                "knowledge_tags": args.knowledge_tags,
                "next_review_at": next_review.isoformat() if next_review else None,
            },
        )
