"""错题提取任务：对话落库后异步判定是否答错，命中则写入错题本。

由 chat_service.stream_chat 在完成流后 delay 触发；任务体 run_extract_mistake
可注入 session_factory / llm，便于离线测试。
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from eduagent_shared import db
from eduagent_shared.config import get_settings
from eduagent_shared.logging import get_logger

from app import prompts
from app.services import chat_service, mistake_service
from app.services.llm_client import get_llm_client
from app.tasks.celery_app import celery_app, run_async

log = get_logger("agent.tasks.mistake")

HISTORY_WINDOW = 8  # 只看最近一轮问答上下文，控制 token


def format_dialogue(messages: Sequence[Any]) -> str:
    """ORM 消息列表 → 判定用对话文本（学生/老师角色前缀）。"""
    lines: list[str] = []
    for message in messages:
        role = getattr(message, "role", "user")
        if role not in {"user", "assistant"}:
            continue
        speaker = "学生" if role == "user" else "老师"
        lines.append(f"{speaker}：{getattr(message, 'content', '')}")
    return "\n".join(lines)


async def run_extract_mistake(
    user_id: str,
    session_id: str,
    subject: Optional[str] = None,
    source_message_id: Optional[str] = None,
    *,
    session_factory: Any = None,
    llm: Any = None,
) -> dict[str, Any]:
    """LLM 判错题（JSON）→ mistake_records + 艾宾浩斯 next_review_at 初始化。"""
    factory = session_factory or db.get_session_factory()
    llm = llm or get_llm_client(get_settings())
    async with factory() as session:
        messages = await chat_service.load_recent_messages(
            session, session_id, limit=HISTORY_WINDOW
        )
        dialogue = format_dialogue(messages)
        if not dialogue.strip():
            return {"created": False, "reason": "empty_dialogue"}
        try:
            result = await llm.chat_json(
                "你是错题判定器，输出严格 JSON。",
                prompts.MISTAKE_ANALYSIS_PROMPT.format(dialogue=dialogue),
            )
        except Exception as exc:  # noqa: BLE001 - 判定失败不阻断主流程
            log.warning("mistake_extract_llm_error", error=str(exc))
            await session.rollback()
            return {"created": False, "reason": "llm_error"}
        if not isinstance(result, dict) or not result.get("is_mistake"):
            return {"created": False}
        record = await mistake_service.record_mistake(
            session, user_id, result, subject=subject, source_message_id=source_message_id
        )
        await session.commit()
        log.info("mistake_extracted", user_id=user_id, mistake_id=str(record.id))
        return {"created": True, "mistake_id": str(record.id)}


@celery_app.task(name="agent.mistake.extract")
def extract_mistake(
    user_id: str,
    session_id: str,
    subject: Optional[str] = None,
    source_message_id: Optional[str] = None,
) -> dict[str, Any]:
    """聊天后异步错题提取（Celery 入口）。"""
    return run_async(run_extract_mistake(user_id, session_id, subject, source_message_id))
