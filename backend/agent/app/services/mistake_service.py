"""错题服务：艾宾浩斯复习调度、复习打卡、变式练习。

艾宾浩斯间隔（天）：1/2/4/7/15/30。约定 review_count 为"已完成复习次数"：
- 新错题 review_count=0，首次复习时间 = 录入时间 + 1 天；
- 第 n 次复习后 review_count=n，下次复习 = 本次时间 + INTERVALS[min(n, 5)]。

字段映射（对齐 shared.models.MistakeRecord）：
LLM 输出的 user_answer → ORM wrong_answer；knowledge_tags → ORM knowledge_points；
掌握度写入 ORM mastery（0~1），由 update_mastery 按复习结果更新。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Optional

from sqlalchemy import select

from eduagent_shared.models import MistakeRecord

from app import prompts
from app.services.llm_client import LLMClient

EBBINGHAUS_INTERVALS_DAYS: list[int] = [1, 2, 4, 7, 15, 30]
MASTERED_REVIEW_COUNT = len(EBBINGHAUS_INTERVALS_DAYS)  # 完成全部周期视为掌握
MASTERED_THRESHOLD = 0.9


def compute_next_review_at(
    review_count: int, base: Optional[datetime] = None
) -> datetime:
    """按艾宾浩斯间隔计算下次复习时间（纯函数）。

    review_count: 已完成的复习次数（新错题传 0）。
    """
    base = base or datetime.now(timezone.utc)
    index = min(max(review_count, 0), len(EBBINGHAUS_INTERVALS_DAYS) - 1)
    return base + timedelta(days=EBBINGHAUS_INTERVALS_DAYS[index])


def update_mastery(mastery: float, correct: bool) -> float:
    """复习后掌握度更新（纯函数）：答对按 40% 逼近 1，答错减半。"""
    mastery = max(0.0, min(1.0, float(mastery)))
    if correct:
        return round(mastery + (1.0 - mastery) * 0.4, 4)
    return round(mastery * 0.5, 4)


async def record_mistake(
    session: Any,
    user_id: str,
    data: dict[str, Any],
    subject: Optional[str] = None,
    source_message_id: Optional[str] = None,
) -> Any:
    """错题落库，初始化艾宾浩斯调度。"""
    record = MistakeRecord(
        user_id=user_id,
        subject=subject or data.get("subject"),
        question=str(data.get("question", "")),
        wrong_answer=str(data.get("user_answer", "")),  # LLM 字段 user_answer → ORM wrong_answer
        correct_answer=str(data.get("correct_answer", "")),
        analysis=str(data.get("analysis", "")),
        knowledge_points=[str(t) for t in data.get("knowledge_tags") or []],
        mastery=0.0,
        source="chat",
        review_count=0,
        next_review_at=compute_next_review_at(0),
    )
    if source_message_id:
        record.source_message_id = source_message_id
    session.add(record)
    await session.flush()
    return record


async def list_mistakes(
    session: Any,
    user_id: str,
    subject: Optional[str] = None,
    tag: Optional[str] = None,
    due: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[Any]:
    """错题列表：按学科/知识点标签过滤，due=true 时只返回到期错题。"""
    stmt = select(MistakeRecord).where(MistakeRecord.user_id == user_id)
    if subject:
        stmt = stmt.where(MistakeRecord.subject == subject)
    if tag:
        stmt = stmt.where(MistakeRecord.knowledge_points.contains([tag]))
    if due:
        stmt = stmt.where(MistakeRecord.next_review_at <= datetime.now(timezone.utc))
    stmt = stmt.order_by(MistakeRecord.next_review_at.asc()).limit(limit).offset(offset)
    rows = await session.execute(stmt)
    return list(rows.scalars().all())


async def review_mistake(
    session: Any, mistake_id: str, user_id: str, correct: bool = True
) -> Optional[Any]:
    """复习打卡：更新 review_count / mastery / next_review_at。

    答错时除掌握度减半外，下次复习强制提前到 1 天后。
    """
    record = await session.get(MistakeRecord, mistake_id)
    if record is None or str(record.user_id) != str(user_id):
        return None
    record.review_count = int(record.review_count or 0) + 1
    record.mastery = update_mastery(float(record.mastery or 0.0), correct)
    if correct:
        record.next_review_at = compute_next_review_at(record.review_count)
    else:
        record.next_review_at = datetime.now(timezone.utc) + timedelta(days=1)
    await session.flush()
    return record


async def stream_variations(
    llm: LLMClient, mistake: Any, model: Optional[str] = None
) -> AsyncIterator[str]:
    """LLM 生成变式练习（SSE 流式透传文本增量）。"""
    prompt = prompts.VARIATION_PROMPT.format(
        question=getattr(mistake, "question", ""),
        correct_answer=getattr(mistake, "correct_answer", "") or "",
        analysis=getattr(mistake, "analysis", "") or "",
        knowledge_tags="、".join(getattr(mistake, "knowledge_points", None) or []),
    )
    async for delta in llm.chat_stream(
        [
            {"role": "system", "content": "你是出题专家，按约定格式输出。"},
            {"role": "user", "content": prompt},
        ],
        model=model,
    ):
        yield delta
