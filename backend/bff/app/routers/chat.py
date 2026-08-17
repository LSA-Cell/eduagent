"""聊天路由：会话/消息 REST 与聊天发起（转发推理网关，SSE 透传）。

安全边界：BFF 只允许访问 INFERENCE_GATEWAY_URL，禁止直连 vLLM。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse

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
from ..services.store import ChatStore

router = APIRouter(prefix="/chat", tags=["chat"])
log = get_logger("bff.chat")


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


def _last_user_text(messages) -> str:
    """取最后一条 user 消息的纯文本（多模态分段取 text 部分拼接）。"""
    for message in reversed(messages):
        if message.role != "user":
            continue
        if isinstance(message.content, str):
            return message.content
        if isinstance(message.content, list):
            return "".join(
                str(part.get("text", ""))
                for part in message.content
                if isinstance(part, dict) and part.get("type") == "text"
            )
    return ""


def _extract_nonstream_text(content: bytes) -> str:
    """从非流式 OpenAI 响应中提取 assistant 文本。"""
    try:
        payload = json.loads(content)
        return payload["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError):
        return ""


def _extract_delta_text(chunk: bytes) -> str:
    """从 SSE chunk 中提取增量文本（用于持久化 assistant 消息）。"""
    text = ""
    for line in chunk.decode("utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
            text += payload["choices"][0].get("delta", {}).get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError):
            continue
    return text


async def _persist_stream(
    upstream: AsyncIterator[bytes],
    chat_store: ChatStore,
    session_id: uuid.UUID,
    model: str | None,
) -> AsyncIterator[bytes]:
    """透传 SSE 的同时累积 assistant 文本，流结束后落库。"""
    collected: list[str] = []
    async for chunk in upstream:
        collected.append(_extract_delta_text(chunk))
        yield chunk
    text = "".join(collected).strip()
    if text:
        try:
            await chat_store.add_message(
                session_id=session_id,
                role="assistant",
                content=text,
                metadata_={"model": model} if model else {},
            )
        except Exception as exc:  # 持久化失败不影响已下发的流
            log.warning("persist_assistant_message_error", error=str(exc))


@router.post("/completions")
async def chat_completions(
    payload: ChatCompletionRequest,
    user=Depends(get_current_user),
    client: httpx.AsyncClient = Depends(get_http_client),
    chat_store: ChatStore = Depends(get_chat_store),
):
    """发起聊天：转发推理网关；stream=true 时 SSE 逐 chunk 透传。

    若携带 session_id，则持久化最后一条用户消息与（流式聚合后的）assistant 回复。
    """
    settings = get_settings()
    session = None
    if payload.session_id is not None:
        session = await chat_store.get_session(payload.session_id)
        if session is None or session.user_id != user.id:
            raise AppError(404, "session_not_found", "会话不存在")

    body = payload.model_dump(exclude_none=True, exclude={"session_id"})
    body["user_id"] = str(user.id)  # 服务端注入，客户端不可伪造

    if session is not None:
        last_text = _last_user_text(payload.messages)
        if last_text:
            await chat_store.add_message(
                session_id=session.id, role="user", content=last_text
            )

    url = f"{settings.inference_gateway_url}/v1/chat/completions"
    headers = {"X-Internal-Token": settings.internal_service_token}

    if not payload.stream:
        try:
            resp = await client.post(url, json=body, headers=headers, timeout=120.0)
        except httpx.HTTPError as exc:
            log.warning("gateway_forward_error", error=str(exc))
            raise AppError(502, "gateway_unavailable", "推理网关暂不可用") from exc
        if session is not None and resp.status_code == 200:
            text = _extract_nonstream_text(resp.content)
            if text:
                await chat_store.add_message(
                    session_id=session.id,
                    role="assistant",
                    content=text,
                    metadata_={"model": body.get("model")} if body.get("model") else {},
                )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )

    try:
        upstream = await client.send(
            client.build_request("POST", url, json=body, headers=headers), stream=True
        )
    except httpx.HTTPError as exc:
        log.warning("gateway_stream_error", error=str(exc))
        raise AppError(502, "gateway_unavailable", "推理网关暂不可用") from exc

    iterator: AsyncIterator[bytes] = upstream.aiter_raw()
    if session is not None and upstream.status_code == 200:
        iterator = _persist_stream(iterator, chat_store, session.id, body.get("model"))

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in iterator:
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        stream(),
        status_code=upstream.status_code,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
