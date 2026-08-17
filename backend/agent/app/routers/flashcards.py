"""记忆卡片路由：CRUD + LLM 批量生成 + SM-2 复习（记得/模糊/忘记）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from eduagent_shared.models import Flashcard

from app import deps
from app.schemas import (
    FlashcardCreateRequest,
    FlashcardGenerateRequest,
    FlashcardReviewRequest,
    FlashcardUpdateRequest,
)
from app.services import flashcard_service
from app.services.llm_client import LLMClient

router = APIRouter(
    prefix="/flashcards",
    tags=["flashcards"],
    dependencies=[Depends(deps.require_internal_auth)],
)


async def _get_owned(db: AsyncSession, card_id: str, user_id: str) -> Any:
    """取卡片并校验归属，不存在/非本人一律 404。"""
    card = await db.get(Flashcard, card_id)
    if card is None or str(card.user_id) != str(user_id):
        raise HTTPException(status_code=404, detail="flashcard not found")
    return card


@router.get("")
async def list_flashcards(
    deck: Optional[str] = Query(default=None, description="卡片组（deck）过滤"),
    due: bool = Query(default=True, description="true 时只返回到期卡片"),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """卡片列表：默认只看到期卡片。"""
    cards = await flashcard_service.list_due_flashcards(
        db, user_id, deck=deck, due_only=due, limit=limit
    )
    return {"items": [flashcard_service.flashcard_to_dict(c) for c in cards]}


@router.post("", status_code=201)
async def create_flashcard(
    req: FlashcardCreateRequest,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """手动创建卡片（新卡立即到期可学）。"""
    card = Flashcard(
        user_id=user_id,
        subject=req.subject,
        deck=req.deck,
        front=req.front,
        back=req.back,
        next_review_at=datetime.now(timezone.utc),
    )
    db.add(card)
    await db.flush()
    await db.commit()
    return flashcard_service.flashcard_to_dict(card)


@router.post("/generate", status_code=201)
async def generate_flashcards(
    req: FlashcardGenerateRequest,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
    llm: LLMClient = Depends(deps.get_llm),
) -> dict[str, Any]:
    """LLM 从原始内容批量生成卡片，返回 {"group_id", "created", "cards"}。"""
    try:
        result = await flashcard_service.run_flashcard_generation(
            db, llm, user_id, content=req.content, count=req.count, subject=req.subject
        )
    except Exception as exc:  # noqa: BLE001 - LLM 输出非法/网关异常
        await db.rollback()
        raise HTTPException(status_code=502, detail=f"卡片生成失败: {str(exc)[:200]}") from exc
    await db.commit()
    return result


@router.get("/{card_id}")
async def get_flashcard(
    card_id: str,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """卡片详情。"""
    return flashcard_service.flashcard_to_dict(await _get_owned(db, card_id, user_id))


@router.patch("/{card_id}")
async def update_flashcard(
    card_id: str,
    req: FlashcardUpdateRequest,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """更新卡片内容（仅提交非空字段）。"""
    card = await _get_owned(db, card_id, user_id)
    for field, value in req.model_dump(exclude_none=True).items():
        setattr(card, field, value)
    await db.flush()
    await db.commit()
    return flashcard_service.flashcard_to_dict(card)


@router.delete("/{card_id}", status_code=204)
async def delete_flashcard(
    card_id: str,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> Response:
    """删除卡片。"""
    card = await _get_owned(db, card_id, user_id)
    await db.delete(card)
    await db.commit()
    return Response(status_code=204)


@router.post("/{card_id}/review")
async def review_flashcard(
    card_id: str,
    req: FlashcardReviewRequest,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """SM-2 复习：记得(good)/模糊(fuzzy)/忘记(again) → ease_factor/interval/due_date。"""
    try:
        card = await flashcard_service.review_flashcard(db, card_id, user_id, req.rating)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if card is None:
        raise HTTPException(status_code=404, detail="flashcard not found")
    await db.commit()
    return flashcard_service.flashcard_to_dict(card)
