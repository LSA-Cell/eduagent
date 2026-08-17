"""聊天编排：历史加载 → 记忆注入 → RAG → 工具循环 → SSE 透传 → 落库 + 异步任务。

时序约束：同步路径只做网关流式透传（SSE 不算阻塞）；错题提取/记忆提取
一律 Celery 异步任务，接口完成流后即返回。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator, Optional, Sequence

from sqlalchemy import select

from eduagent_shared.models import ChatSession, Message, User

from app import prompts
from app.memory import memory_manager
from app.memory.embeddings import EmbeddingProvider
from app.services import rag_service
from app.services.llm_client import ChatResult, LLMClient
from app.tools.base import ToolContext, ToolRegistry

MAX_TOOL_ROUNDS = 5
HISTORY_LIMIT = 20
HISTORY_CONTENT_MAX = 2000  # 单条历史截断，控制 token


# ---------------------------------------------------------------------------
# 会话与历史
# ---------------------------------------------------------------------------
async def get_or_create_session(
    session: Any, user_id: str, session_id: Optional[str], subject: Optional[str],
    model: Optional[str], title_seed: str,
) -> Any:
    """按 session_id 取会话（校验归属），否则新建。"""
    if session_id:
        chat_session = await session.get(ChatSession, session_id)
        if chat_session is not None and str(chat_session.user_id) == str(user_id):
            return chat_session
    chat_session = ChatSession(
        user_id=user_id,
        subject=subject,
        model=model,
        title=title_seed[:50],
    )
    session.add(chat_session)
    await session.flush()
    return chat_session


async def load_recent_messages(session: Any, session_id: Any, limit: int = HISTORY_LIMIT) -> list[Any]:
    """加载会话最近 N 条消息，按时间正序返回。"""
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    rows = await session.execute(stmt)
    return list(reversed(rows.scalars().all()))


def messages_to_prompt(messages: Sequence[Any]) -> list[dict[str, str]]:
    """ORM 消息 → OpenAI messages 格式（截断超长内容）。"""
    result: list[dict[str, str]] = []
    for msg in messages:
        role = getattr(msg, "role", "user")
        if role not in {"user", "assistant", "system"}:
            continue
        content = str(getattr(msg, "content", ""))[:HISTORY_CONTENT_MAX]
        result.append({"role": role, "content": content})
    return result


async def save_message(
    session: Any, session_id: Any, role: str, content: str,
    metadata: Optional[dict[str, Any]] = None,
) -> Any:
    """消息落库（metadata 透传，如 RAG 引用/工具轨迹）。"""
    message = Message(
        session_id=session_id,
        role=role,
        content=content,
        metadata_=metadata or {},
    )
    session.add(message)
    await session.flush()
    return message


async def load_user(session: Any, user_id: str) -> Any:
    """加载用户（供 system prompt 组装）；不存在时返回鸭子类型空对象。"""

    class _AnonymousUser:
        grade_level = None
        default_subject = None
        nickname = None

    try:
        user = await session.get(User, user_id)
    except Exception:  # noqa: BLE001 - 非 UUID 等场景兜底
        user = None
    return user or _AnonymousUser()


# ---------------------------------------------------------------------------
# 工具调用循环
# ---------------------------------------------------------------------------
async def run_tool_loop(
    llm: LLMClient,
    messages: list[dict[str, Any]],
    registry: ToolRegistry,
    ctx: ToolContext,
    *,
    model: Optional[str] = None,
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """tool_use 循环：LLM 返回 tool_calls → 执行 → 回填 → 再调，最多 max_rounds 轮。

    返回 (最终文本, 完整消息列表, 工具调用轨迹)。
    达到上限后做一次无工具的兜底调用，保证总有文本回复。
    """
    msgs = list(messages)
    traces: list[dict[str, Any]] = []
    for _ in range(max_rounds):
        result = await llm.chat(msgs, model=model, tools=registry.openai_schemas())
        if not result.tool_calls:
            return result.content, msgs, traces
        msgs.append(_assistant_tool_message(result))
        for call in result.tool_calls:
            output = await registry.execute(call.name, call.arguments, ctx)
            traces.append({"name": call.name, "arguments": call.arguments, "output": output})
            msgs.append({
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": json.dumps(output, ensure_ascii=False, default=str),
            })
    # 达到上限：无工具兜底，避免无限循环
    final = await llm.chat(msgs, model=model, tools=None)
    return final.content, msgs, traces


def _assistant_tool_message(result: ChatResult) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": result.content or "",
        "tool_calls": [
            {"id": call.id, "type": "function",
             "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)}}
            for call in result.tool_calls
        ],
    }


# ---------------------------------------------------------------------------
# RAG 注入
# ---------------------------------------------------------------------------
async def build_rag_context(
    session: Any,
    provider: Optional[EmbeddingProvider],
    query: str,
    subject: Optional[str] = None,
    top_k: int = 5,
) -> tuple[str, list[dict[str, Any]]]:
    """检索知识库，返回 (拼入 prompt 的文本块, 引用列表)。无命中/异常时返回空。"""
    embedding: Optional[list[float]] = None
    if provider is not None:
        try:
            embedding = (await provider.embed([query]))[0]
        except Exception:  # noqa: BLE001 - embedding 失败降级为纯关键词检索
            embedding = None
    chunks = await rag_service.hybrid_search(
        session, query, query_embedding=embedding, subject=subject, top_k=top_k
    )
    if not chunks:
        return "", []
    lines = [
        f"[{i}] {chunk.content}"
        for i, chunk in enumerate(chunks, start=1)
    ]
    citations = [{"ref": i, **chunk.citation} for i, chunk in enumerate(chunks, start=1)]
    return prompts.RAG_CONTEXT_TEMPLATE.format(chunks="\n\n".join(lines)), citations


# ---------------------------------------------------------------------------
# 主编排：流式聊天
# ---------------------------------------------------------------------------
async def stream_chat(
    session: Any,
    settings: Any,
    llm: LLMClient,
    registry: ToolRegistry,
    provider: Optional[EmbeddingProvider],
    req: Any,
) -> AsyncIterator[dict[str, Any]]:
    """聊天主流程（异步生成器，产出 SSE 事件字典，由路由层编码）。

    事件：session / delta* / done / error。
    完成后：落库 user+assistant 消息，触发 extract_mistake / extract_memories。
    """
    # 延迟导入避免循环（tasks 依赖本模块的服务函数）
    from app.tasks import memory_tasks, mistake_tasks

    chat_session = await get_or_create_session(
        session, req.user_id, req.session_id, req.subject, req.model, req.message
    )
    session_id = str(chat_session.id)
    yield {"type": "session", "session_id": session_id}

    user = await load_user(session, req.user_id)
    history = messages_to_prompt(await load_recent_messages(session, chat_session.id))

    # 记忆检索与注入（embedding 失败不影响主流程）
    memories: list[memory_manager.MemoryItem] = []
    if provider is not None:
        try:
            query_vec = (await provider.embed([req.message]))[0]
            memories = await memory_manager.retrieve_memories(
                session, req.user_id, query_embedding=query_vec
            )
        except Exception:  # noqa: BLE001
            memories = []
    system_prompt = memory_manager.build_system_prompt(user, memories)

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(history)

    # RAG 检索（开启时）
    citations: list[dict[str, Any]] = []
    user_content = req.message
    if req.enable_rag:
        try:
            rag_text, citations = await build_rag_context(
                session, provider, req.message, subject=req.subject
            )
        except Exception:  # noqa: BLE001 - 检索失败降级为无 RAG
            rag_text, citations = "", []
        if rag_text:
            messages.append({"role": "system", "content": rag_text})
    messages.append({"role": "user", "content": user_content})

    full_text = ""
    tool_traces: list[dict[str, Any]] = []
    try:
        if req.enable_tools:
            ctx = ToolContext(
                user_id=req.user_id, session_id=session_id,
                session=session, settings=settings, llm=llm,
            )
            full_text, _, tool_traces = await run_tool_loop(
                llm, messages, registry, ctx, model=req.model
            )
            if full_text:
                yield {"type": "delta", "content": full_text}
        else:
            async for delta in llm.chat_stream(messages, model=req.model):
                full_text += delta
                yield {"type": "delta", "content": delta}
    except Exception as exc:  # noqa: BLE001 - 网关异常需转成 SSE 错误事件
        yield {"type": "error", "message": str(exc)[:300]}
        return

    # 落库（user + assistant 两条）
    await save_message(session, chat_session.id, "user", req.message)
    assistant_meta: dict[str, Any] = {}
    if citations:
        assistant_meta["citations"] = citations
    if tool_traces:
        assistant_meta["tool_traces"] = tool_traces
    assistant_msg = await save_message(
        session, chat_session.id, "assistant", full_text, metadata=assistant_meta
    )

    # 异步任务：错题提取 + 记忆提取（不阻塞响应）
    mistake_tasks.extract_mistake.delay(
        user_id=str(req.user_id), session_id=session_id, subject=req.subject,
        source_message_id=str(assistant_msg.id),
    )
    memory_tasks.extract_memories.delay(user_id=str(req.user_id), session_id=session_id)

    yield {
        "type": "done",
        "session_id": session_id,
        "message_id": str(assistant_msg.id),
        "citations": citations,
        "tool_calls": [t["name"] for t in tool_traces],
    }


def sse_encode(event: dict[str, Any]) -> str:
    """SSE 帧编码。"""
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def new_request_id() -> str:
    return uuid.uuid4().hex
