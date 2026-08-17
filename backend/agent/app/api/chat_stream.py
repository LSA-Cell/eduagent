"""POST /v1/chat/stream：统一 SSE 事件协议的新聊天端点（edu_agent_loop 驱动）。

- 事件协议见设计文档 §四（9 类事件，events/sse.py 编码，id=seq）；
- 断线重放：客户端携带 ``Last-Event-ID`` + ``?run_id=`` 时进入重放模式，
  从 replay store 补发其后事件（不重跑 Loop）；
- 消息落库复用旧 chat_service（get_or_create_session / save_message）；
- 完成后异步触发错题提取 + 记忆提取（deps.celery_send，不阻塞响应）；
- 鉴权：X-Internal-Token（BFF 内部令牌）+ X-User-Id（目标用户，覆盖 body）。

生产部署注意：role/is_minor 等身份字段当前由请求体透传（BFF 应依据用户
记录填写）；is_teacher 由 role=="teacher" 派生，不信任客户端布尔位。
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from app import deps as app_deps
from app.events.protocol import EventType, SSEEvent
from app.events.sse import sse_encode
from app.schemas import ChatStreamRequest
from app.services import chat_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1",
    tags=["orchestration"],
    dependencies=[Depends(app_deps.require_internal_auth)],
)

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


async def _ensure_session(orch_deps: Any, req: ChatStreamRequest) -> Optional[str]:
    """创建/复用会话（失败降级：不阻塞流，仅跳过历史与落库）。"""
    try:
        async with orch_deps.session_factory() as session:
            chat_session = await chat_service.get_or_create_session(
                session, req.user_id, req.session_id, req.subject, req.model, req.message
            )
            await session.commit()
        return str(chat_session.id)
    except Exception:  # noqa: BLE001 - 会话不可用降级为无历史无落库
        logger.warning("会话创建/加载失败，降级为无会话模式", exc_info=True)
        return req.session_id


async def _persist_turn(
    orch_deps: Any,
    session_id: Optional[str],
    req: ChatStreamRequest,
    assistant_text: str,
    cards: list[dict[str, Any]],
    *,
    interrupted: bool = False,
) -> None:
    """落库 user+assistant 两条消息并派后处理任务（全部 fail-soft）。

    打断的半截消息必须带 interrupted=true 落库（红线：禁止以完整消息呈现）。
    """
    if not session_id:
        return
    try:
        async with orch_deps.session_factory() as session:
            await chat_service.save_message(session, session_id, "user", req.message)
            metadata: dict[str, Any] = {"cards": cards} if cards else {}
            if interrupted:
                metadata["interrupted"] = True
            assistant_msg = await chat_service.save_message(
                session, session_id, "assistant", assistant_text, metadata=metadata
            )
            await session.commit()
            source_message_id = str(getattr(assistant_msg, "id", "") or "") or None
    except Exception:  # noqa: BLE001 - 落库失败不反向影响已完成的流
        logger.warning("消息落库失败", exc_info=True)
        return
    try:
        orch_deps.celery_send("agent.mistake.extract", {
            "user_id": str(req.user_id),
            "session_id": session_id,
            "subject": req.subject,
            "source_message_id": source_message_id,
        })
        orch_deps.celery_send("agent.memory.extract", {
            "user_id": str(req.user_id),
            "session_id": session_id,
        })
    except Exception:  # noqa: BLE001 - 后处理任务派发失败仅记日志
        logger.warning("后处理任务派发失败", exc_info=True)


@router.post("/chat/stream")
async def chat_stream(
    req: ChatStreamRequest,
    x_user_id: str = Header(default="", alias="X-User-Id"),
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    run_id: Optional[str] = Query(default=None),
    orch_deps: Any = Depends(app_deps.get_orchestration_deps),
) -> StreamingResponse:
    """聊天主入口（text/event-stream，事件契约见 docs/api/asyncapi.yaml）。"""
    user_id = x_user_id or req.user_id
    if not user_id:
        raise HTTPException(status_code=400, detail="missing user id")

    # ---- 断线重放模式：Last-Event-ID + run_id → 补发其后事件（不重跑 Loop） ----
    if last_event_id is not None:
        if not run_id:
            raise HTTPException(status_code=400, detail="Last-Event-ID 重放需携带 run_id")
        try:
            last_seq = int(last_event_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Last-Event-ID 必须是整数 seq") from None

        async def replay_stream() -> AsyncGenerator[str, None]:
            if orch_deps.replay is None:
                return
            for event in await orch_deps.replay.replay_since(run_id, last_seq):
                yield sse_encode(event)

        return StreamingResponse(
            replay_stream(), media_type="text/event-stream", headers=_SSE_HEADERS
        )

    req = req.model_copy(update={
        "user_id": user_id,
        "is_teacher": req.role == "teacher",  # 身份布尔位由 role 派生
    })
    session_id = await _ensure_session(orch_deps, req)
    if session_id:
        req = req.model_copy(update={"session_id": session_id})

    async def event_stream() -> AsyncGenerator[str, None]:
        from app.loop.agent_loop import edu_agent_loop

        text_parts: list[str] = []
        cards: list[dict[str, Any]] = []
        last_seq = 0
        interrupted = False
        try:
            async for event in edu_agent_loop(req, orch_deps):
                last_seq = max(last_seq, event.seq)
                if event.event == EventType.MESSAGE_DELTA.value:
                    text_parts.append(str(event.data.get("text", "")))
                elif event.event == EventType.CARD.value:
                    cards.append({
                        "card_type": event.data.get("card_type"),
                        "card_data": event.data.get("card_data"),
                        "file_urls": event.data.get("file_urls") or [],
                    })
                elif event.event == EventType.RUN_INTERRUPTED.value:
                    interrupted = True
                yield sse_encode(event)
        except Exception:  # noqa: BLE001 - Loop 兜底之外：转成可读 error 帧
            logger.warning("chat stream 异常", exc_info=True)
            yield sse_encode(SSEEvent(
                event=EventType.ERROR.value,
                data={"seq": last_seq + 1, "code": "internal_error",
                      "message": "服务内部错误，请重试或开启新会话。"},
            ))
        await _persist_turn(
            orch_deps, session_id, req, "".join(text_parts), cards, interrupted=interrupted
        )

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS
    )
