"""flashcard_generator 工具：LLM 从内容批量生成记忆卡片并落库（写工具）。

副作用声明：经 ctx.deps.session_factory 写 flashcards 表（并提交事务）；
生成逻辑复用 app.services.flashcard_service.run_flashcard_generation
（SM-2 初始化 + LLM 生成）。LLM 访问只经 ctx.deps.gateway（推理网关），
_GatewayJsonClient 把 chat 文本适配为 chat_json 契约。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.tools.base import EduToolBase, ToolContext, ToolResult


class FlashcardGeneratorArgs(BaseModel):
    content: str = Field(min_length=1, description="用于生成卡片的原始内容（课文/笔记/知识点）")
    count: int = Field(default=10, ge=1, le=50)
    subject: Optional[str] = Field(default=None, description="学科（缺省取当前会话学科）")
    deck: Optional[str] = Field(default=None, description="卡片组名（缺省自动生成）")


class _GatewayJsonClient:
    """GatewayClient.chat → flashcard_service 期望的 chat_json 契约适配。"""

    def __init__(self, gateway: Any) -> None:
        self._gateway = gateway

    async def chat_json(self, system: str, user: str, **kwargs: Any) -> Any:
        result = await self._gateway.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )
        text = (getattr(result, "content", "") or "").strip()
        if text.startswith("```"):  # 剥离 markdown 代码围栏
            text = text.strip("`")
            if text[:4].lower() == "json":
                text = text[4:]
        return json.loads(text.strip())


class FlashcardGeneratorTool(EduToolBase):
    """记忆卡片生成（写）：LLM 产出卡片组并落库，返回 flashcard_group 卡片。"""

    name = "flashcard_generator"
    description = "根据学习内容自动生成一组记忆卡片（正面问题/背面答案），写入用户卡片库。"
    input_model = FlashcardGeneratorArgs
    is_read_only = False
    is_concurrency_safe = False
    card_type = "flashcard_group"
    timeout_s = 30  # 含一次 LLM 生成

    async def call(self, args: FlashcardGeneratorArgs, ctx: ToolContext) -> ToolResult:
        from app.services import flashcard_service

        llm = ctx.llm or _GatewayJsonClient(ctx.deps.gateway)
        subject = args.subject or getattr(ctx, "subject", None)
        async with ctx.deps.session_factory() as session:
            outcome = await flashcard_service.run_flashcard_generation(
                session, llm, ctx.user_id,
                content=args.content, count=args.count,
                subject=subject, deck=args.deck,
            )
            await session.commit()
        created = int(outcome.get("created", 0))
        group_id = str(outcome.get("group_id", ""))
        cards = list(outcome.get("cards") or [])
        return ToolResult(
            content=f"已生成 {created} 张记忆卡片（卡片组 {group_id}），可立即开始复习。",
            card_type=self.card_type,
            card_data={"group_id": group_id, "created": created, "cards": cards},
        )
