"""聊天路由：会话/消息 REST + 聊天统一入口（转发 Agent 编排层）。

红线：
- 聊天一律经 Agent 编排服务（POST {AGENT}/v1/chat/stream），BFF 禁止
  绕过编排直连推理网关；LLM 出口只在 Agent → Gateway 一段；
- 消息/会话写库由 Agent 统一负责（BFF 只做归属校验），消除双落库；
- /chat/completions 保持 OpenAI 兼容契约（旧移动端）；/chat/stream 原样
  透传 Agent 9 类事件（支持 Last-Event-ID 断线重放），供新客户端使用。
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from eduagent_shared.config import get_settings
from eduagent_shared.logging import get_logger

from ..deps import get_chat_store, get_current_user, get_http_client
from ..errors import AppError
from ..schemas import (
    ChatCompletionRequest,
    MessageOut,
    Page,
    SessionCreate,
    SessionOut,
)
from ..services import agent_chat
from ..services.store import ChatStore

router = APIRouter(prefix="/chat", tags=["chat"])
log = get_logger("bff.chat")

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.post("/sessions", response_model=SessionOut, status_code=201)
async def create_session(
    payload: SessionCreate,
    user=Depends(get_current_user),
    chat_store: ChatStore = Depends(get_chat_store),
) -> SessionOut:
    """创建聊天会话。"""
    session = await chat_store.create_session(
        user_id=user.id,
        title=payload.title,
        subject=payload.subject,
        model=payload.model,
        inference_source=payload.inference_source,
    )
    return SessionOut.model_validate(session)


@router.get("/sessions", response_model=Page[SessionOut])
async def list_sessions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user=Depends(get_current_user),
    chat_store: ChatStore = Depends(get_chat_store),
) -> Page[SessionOut]:
    """分页列出当前用户会话（按更新时间倒序）。"""
    items, total = await chat_store.list_sessions(user.id, page, page_size)
    return Page(
        items=[SessionOut.model_validate(s) for s in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/sessions/{session_id}/messages", response_model=Page[MessageOut])
async def list_messages(
    session_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user=Depends(get_current_user),
    chat_store: ChatStore = Depends(get_chat_store),
) -> Page[MessageOut]:
    """分页列出会话消息（按时间升序）。"""
    session = await chat_store.get_session(session_id)
    if session is None or session.user_id != user.id:
        raise AppError(404, "session_not_found", "会话不存在")
    items, total = await chat_store.list_messages(session_id, page, page_size)
    return Page(
        items=[MessageOut.model_validate(m) for m in items],
        total=total,
        page=page,
        page_size=page_size,
    )


async def _check_session_owner(
    payload: ChatCompletionRequest, user: Any, chat_store: ChatStore
) -> None:
    """携带 session_id 时校验归属（行为契约：他人会话 404）。"""
    if payload.session_id is None:
        return
    session = await chat_store.get_session(payload.session_id)
    if session is None or session.user_id != user.id:
        raise AppError(404, "session_not_found", "会话不存在")


async def _open_agent(
    payload: ChatCompletionRequest,
    user: Any,
    client: httpx.AsyncClient,
    *,
    extra_headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    """打开到 Agent 编排层的流式连接；连接失败统一 502。"""
    settings = get_settings()
    body = agent_chat.build_agent_body(payload, user)
    if not body["message"]:
        raise AppError(400, "empty_message", "消息内容不能为空")
    try:
        return await agent_chat.open_agent_stream(
            client, settings, body, user,
            extra_headers=extra_headers, params=params,
        )
    except httpx.HTTPError as exc:
        log.warning("agent_stream_error", error=str(exc))
        raise AppError(502, "agent_unavailable", "编排服务暂不可用") from exc


@router.post("/completions")
async def chat_completions(
    payload: ChatCompletionRequest,
    user=Depends(get_current_user),
    client: httpx.AsyncClient = Depends(get_http_client),
    chat_store: ChatStore = Depends(get_chat_store),
):
    """发起聊天（OpenAI 兼容契约）：转发 Agent 编排层，事件流翻译为 chunk。

    stream=true → SSE 逐 chunk 下发；stream=false → 聚合为 completion JSON。
    会话与消息落库由 Agent 统一完成（本端点不写 messages）。
    """
    await _check_session_owner(payload, user, chat_store)
    upstream = await _open_agent(payload, user, client)

    if upstream.status_code != 200:
        content = await upstream.aread()
        await upstream.aclose()
        return Response(
            content=content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    model = payload.model or "edu-general-14b"
    if not payload.stream:
        try:
            completion = await agent_chat.collect_completion(
                upstream.aiter_raw(), model=model
            )
        finally:
            await upstream.aclose()
        return JSONResponse(completion)

    async def stream():
        try:
            async for chunk in agent_chat.translate_to_openai_chunks(
                upstream.aiter_raw(), model=model
            ):
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        stream(),
        status_code=200,
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/stream")
async def chat_stream_passthrough(
    payload: ChatCompletionRequest,
    request: Request,
    run_id: str | None = Query(default=None),
    user=Depends(get_current_user),
    client: httpx.AsyncClient = Depends(get_http_client),
    chat_store: ChatStore = Depends(get_chat_store),
):
    """发起聊天（新事件协议）：Agent 9 类 SSE 事件原样透传。

    断线重放：携带 ``Last-Event-ID`` 响应头序 + ``?run_id=`` 时由 Agent
    补发其后事件（不重跑 Loop）。
    """
    await _check_session_owner(payload, user, chat_store)

    extra_headers: dict[str, str] = {}
    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id:
        extra_headers["Last-Event-ID"] = last_event_id
    params = {"run_id": run_id} if run_id else None

    upstream = await _open_agent(
        payload, user, client, extra_headers=extra_headers, params=params
    )

    async def stream():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        stream(),
        status_code=upstream.status_code,
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
