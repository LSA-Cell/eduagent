"""记忆卡片 SRS：SM-2 简化版 + LLM 批量生成卡片。

字段映射（对齐 shared.models.Flashcard）：
到期时间写 next_review_at 列；卡片组用 deck 字符串承载
（生成接口的 group_id 即 deck 名，便于按组查询）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy import select

from eduagent_shared.models import Flashcard

from app import prompts
from app.services.content_gen import FlashcardSpec
from app.services.llm_client import LLMClient

MIN_EASE_FACTOR = 1.3
DEFAULT_EASE_FACTOR = 2.5

# 三档评分 → SM-2 quality（0-5）
RATING_QUALITY: dict[str, int] = {"again": 1, "fuzzy": 3, "good": 5}


def sm2_update(
    ease_factor: float,
    repetitions: int,
    interval_days: int,
    quality: int,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """SM-2 简化版状态更新（纯函数）。

    quality >= 3（记得/模糊）：间隔按 1 天 → 6 天 → interval*EF 推进；
    quality < 3（忘记）：repetitions 归零，间隔回到 1 天；
    ease_factor 按 SM-2 公式调整，下限 1.3。
    返回 {"ease_factor", "repetitions", "interval_days", "due_date"}。
    """
    now = now or datetime.now(timezone.utc)
    quality = max(0, min(5, int(quality)))
    if quality >= 3:
        if repetitions <= 0:
            interval = 1
        elif repetitions == 1:
            interval = 6
        else:
            interval = max(1, round(interval_days * ease_factor))
        repetitions += 1
    else:
        repetitions = 0
        interval = 1
    ease = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    ease = max(MIN_EASE_FACTOR, round(ease, 4))
    return {
        "ease_factor": ease,
        "repetitions": repetitions,
        "interval_days": interval,
        "due_date": now + timedelta(days=interval),
    }


async def review_flashcard(
    session: Any, card_id: str, user_id: str, rating: str
) -> Optional[Any]:
    """卡片复习：记得/模糊/忘记 → 更新 ease_factor、interval、due_date。"""
    card = await session.get(Flashcard, card_id)
    if card is None or str(card.user_id) != str(user_id):
        return None
    quality = RATING_QUALITY.get(rating)
    if quality is None:
        raise ValueError(f"非法评分: {rating}")
    state = sm2_update(
        ease_factor=float(getattr(card, "ease_factor", None) or DEFAULT_EASE_FACTOR),
        repetitions=int(getattr(card, "repetitions", 0) or 0),
        interval_days=int(getattr(card, "interval_days", 0) or 0),
        quality=quality,
    )
    card.ease_factor = state["ease_factor"]
    card.repetitions = state["repetitions"]
    card.interval_days = state["interval_days"]
    card.next_review_at = state["due_date"]
    await session.flush()
    return card


async def list_due_flashcards(
    session: Any, user_id: str, deck: Optional[str] = None,
    due_only: bool = True, limit: int = 50,
) -> list[Any]:
    """卡片列表：默认只返回到期卡片，可按 deck（卡片组）过滤。"""
    stmt = select(Flashcard).where(Flashcard.user_id == user_id)
    if deck:
        stmt = stmt.where(Flashcard.deck == deck)
    if due_only:
        now = datetime.now(timezone.utc)
        stmt = stmt.where((Flashcard.next_review_at <= now) | Flashcard.next_review_at.is_(None))
    stmt = stmt.order_by(Flashcard.next_review_at.asc().nulls_first()).limit(limit)
    rows = await session.execute(stmt)
    return list(rows.scalars().all())


async def run_flashcard_generation(
    session: Any,
    llm: LLMClient,
    user_id: str,
    content: str,
    count: int,
    subject: Optional[str] = None,
    deck: Optional[str] = None,
) -> dict[str, Any]:
    """LLM 从内容生成卡片组并落库，返回 {"group_id", "created", "cards"}。

    group_id 即 deck 名（ORM 无独立组列），GET /flashcards?deck= 可按组查询。
    """
    deck = deck or f"auto-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6]}"
    raw = await llm.chat_json(
        "你是记忆卡片制作专家，输出严格 JSON 数组。",
        prompts.FLASHCARD_PROMPT.format(content=content, count=count),
    )
    if not isinstance(raw, list):
        raise ValueError("LLM 输出不是 JSON 数组")
    cards: list[Flashcard] = []
    for item in raw:
        try:
            spec = FlashcardSpec.model_validate(item)
        except ValidationError:
            continue  # 单张不合格跳过，不影响整组
        card = Flashcard(
            user_id=user_id,
            deck=deck,
            subject=subject,
            front=spec.front,
            back=spec.back,
            ease_factor=DEFAULT_EASE_FACTOR,
            repetitions=0,
            interval_days=0,
            next_review_at=datetime.now(timezone.utc),  # 新卡立即到期可学
        )
        session.add(card)
        cards.append(card)
    await session.flush()
    return {
        "group_id": deck,
        "created": len(cards),
        "cards": [{"front": c.front, "back": c.back} for c in cards],
    }


def flashcard_to_dict(card: Any) -> dict[str, Any]:
    """记录 → API DTO。"""
    return {
        "id": str(card.id), "deck": card.deck, "subject": card.subject,
        "front": card.front, "back": card.back,
        "ease_factor": card.ease_factor, "interval_days": card.interval_days,
        "repetitions": card.repetitions,
        "due_date": card.next_review_at.isoformat() if getattr(card, "next_review_at", None) else None,
        "created_at": card.created_at.isoformat() if getattr(card, "created_at", None) else None,
    }
