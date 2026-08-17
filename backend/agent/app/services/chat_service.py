"""聊天持久化公共助手：会话/消息的读取与落库。

供编排层（api/chat_stream.py、loop/agent_loop.py）与异步任务
（memory_tasks / mistake_tasks / subagent_tasks）复用。

注意：旧的 stream_chat / run_tool_loop 已随 /agent/chat 路由下线删除
（无护栏工具循环违反「工具一律过护栏链」红线；聊天统一由
/v1/chat/stream 的 edu_agent_loop 驱动）。
"""

from __future__ import annotations

import json
from typing import Any, Optional, Sequence

from sqlalchemy import select

from eduagent_shared.models import ChatSession, Message

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
    """消息落库（metadata 透传，如卡片/工具轨迹）。"""
    message = Message(
        session_id=session_id,
        role=role,
        content=content,
        metadata_=metadata or {},
    )
    session.add(message)
    await session.flush()
    return message


def sse_encode(event: dict[str, Any]) -> str:
    """SSE 帧编码（routers/mistakes.py 变体生成等旧式事件流使用）。"""
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
