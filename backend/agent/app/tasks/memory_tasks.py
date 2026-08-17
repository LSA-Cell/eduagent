"""记忆提取任务：对话落库后异步提取值得长期记住的学生事实。

流程：最近对话 → LLM 提取（JSON 数组）→ 归一化/语义去重 → memories 表 + embedding。
由 chat_service.stream_chat 在完成流后 delay 触发。
"""

from __future__ import annotations

from typing import Any, Optional

from eduagent_shared import db
from eduagent_shared.config import get_settings
from eduagent_shared.logging import get_logger

from app import prompts
from app.memory import memory_manager
from app.memory.embeddings import EmbeddingProvider, get_embedding_provider
from app.services import chat_service
from app.services.llm_client import get_llm_client
from app.tasks.celery_app import celery_app, run_async
from app.tasks.mistake_tasks import format_dialogue

log = get_logger("agent.tasks.memory")

HISTORY_WINDOW = 10


async def run_extract_memories(
    user_id: str,
    session_id: str,
    *,
    session_factory: Any = None,
    llm: Any = None,
    provider: Optional[EmbeddingProvider] = None,
) -> dict[str, Any]:
    """LLM 提取事实 → 去重 → 落库，返回 {"saved": n}。"""
    factory = session_factory or db.get_session_factory()
    llm = llm or get_llm_client(get_settings())
    if provider is None:
        provider = get_embedding_provider(get_settings(), llm)
    async with factory() as session:
        messages = await chat_service.load_recent_messages(
            session, session_id, limit=HISTORY_WINDOW
        )
        dialogue = format_dialogue(messages)
        if not dialogue.strip():
            return {"saved": 0, "reason": "empty_dialogue"}
        try:
            facts = await llm.chat_json(
                "你是记忆提取器，输出严格 JSON 数组。",
                prompts.MEMORY_EXTRACTION_PROMPT.format(dialogue=dialogue),
            )
        except Exception as exc:  # noqa: BLE001 - 提取失败不阻断主流程
            log.warning("memory_extract_llm_error", error=str(exc))
            await session.rollback()
            return {"saved": 0, "reason": "llm_error"}
        if not isinstance(facts, list):
            return {"saved": 0, "reason": "bad_llm_output"}
        created = await memory_manager.dedup_and_save_memories(
            session, user_id, facts, provider=provider
        )
        await session.commit()
        log.info("memories_extracted", user_id=user_id, saved=len(created))
        return {"saved": len(created)}


@celery_app.task(name="agent.memory.extract")
def extract_memories(user_id: str, session_id: str) -> dict[str, Any]:
    """聊天后异步记忆提取（Celery 入口）。"""
    return run_async(run_extract_memories(user_id, session_id))
