"""聊天路由：POST /agent/chat（SSE 流式透传）。

流程（主体在 services.chat_service.stream_chat）：加载会话历史 → 记忆注入
system prompt → RAG（可选）→ 工具循环 / 推理网关 SSE → 落库 → 派 Celery 任务。
本层只负责参数解析、SSE 编码与事务提交。
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from eduagent_shared.config import Settings

from app import deps
from app.memory.embeddings import EmbeddingProvider
from app.schemas import ChatRequest
from app.services import chat_service
from app.services.llm_client import LLMClient
from app.tools.base import ToolRegistry

router = APIRouter(
    prefix="/agent",
    tags=["chat"],
    dependencies=[Depends(deps.require_internal_auth)],
)

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.post("/chat")
async def agent_chat(
    req: ChatRequest,
    x_user_id: str = Header(default="", alias="X-User-Id"),
    db: AsyncSession = Depends(deps.get_db),
    settings: Settings = Depends(deps.get_settings),
    llm: LLMClient = Depends(deps.get_llm),
    registry: ToolRegistry = Depends(deps.get_tool_registry),
    provider: Optional[EmbeddingProvider] = Depends(deps.get_embedding_provider),
) -> StreamingResponse:
    """聊天主入口（SSE）。事件帧：session / delta* / done / error。"""
    user_id = x_user_id or req.user_id  # 头优先（BFF 注入），兼容仅 body 传参
    if not user_id:
        raise HTTPException(status_code=400, detail="missing user id")
    req = req.model_copy(update={"user_id": user_id})

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            async for event in chat_service.stream_chat(
                db, settings, llm, registry, provider, req
            ):
                yield chat_service.sse_encode(event)
            await db.commit()
        except Exception as exc:  # noqa: BLE001 - 流内异常统一落成 error 帧
            await db.rollback()
            yield chat_service.sse_encode({"type": "error", "message": str(exc)[:300]})

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS
    )
