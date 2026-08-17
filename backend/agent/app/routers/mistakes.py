"""错题本路由：列表筛选 / 详情 / 复习打卡（艾宾浩斯推进）/ LLM 变式练习（SSE）。"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from eduagent_shared.models import MistakeRecord

from app import deps
from app.schemas import MistakeReviewRequest
from app.services import chat_service, mistake_service
from app.services.llm_client import LLMClient

router = APIRouter(
    prefix="/mistakes",
    tags=["mistakes"],
    dependencies=[Depends(deps.require_internal_auth)],
)


def mistake_to_dict(record: Any) -> dict[str, Any]:
    """MistakeRecord → API DTO。"""
    return {
        "id": str(record.id),
        "subject": record.subject,
        "question": record.question,
        "wrong_answer": record.wrong_answer,
        "correct_answer": record.correct_answer,
        "analysis": record.analysis,
        "knowledge_points": list(record.knowledge_points or []),
        "mastery": record.mastery,
        "review_count": record.review_count,
        "source": record.source,
        "next_review_at": _iso(record.next_review_at),
        "created_at": _iso(record.created_at),
    }


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else None


async def _get_owned(db: AsyncSession, mistake_id: str, user_id: str) -> Any:
    """取错题并校验归属，不存在/非本人一律 404。"""
    record = await db.get(MistakeRecord, mistake_id)
    if record is None or str(record.user_id) != str(user_id):
        raise HTTPException(status_code=404, detail="mistake not found")
    return record


@router.get("")
async def list_mistakes(
    subject: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None, description="知识点标签过滤"),
    due: bool = Query(default=False, description="true 时只返回到期待复习错题"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """错题列表：学科 / 知识点 / 到期筛选。"""
    records = await mistake_service.list_mistakes(
        db, user_id, subject=subject, tag=tag, due=due, limit=limit, offset=offset
    )
    return {"items": [mistake_to_dict(r) for r in records]}


@router.get("/{mistake_id}")
async def get_mistake(
    mistake_id: str,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """错题详情。"""
    return mistake_to_dict(await _get_owned(db, mistake_id, user_id))


@router.post("/{mistake_id}/review")
async def review_mistake(
    mistake_id: str,
    req: MistakeReviewRequest,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """复习打卡：艾宾浩斯 1/2/4/7/15/30 天推进，答错则掌握度减半并提前到 1 天后。"""
    record = await mistake_service.review_mistake(db, mistake_id, user_id, correct=req.correct)
    if record is None:
        raise HTTPException(status_code=404, detail="mistake not found")
    await db.commit()
    return mistake_to_dict(record)


@router.post("/{mistake_id}/variations")
async def mistake_variations(
    mistake_id: str,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
    llm: LLMClient = Depends(deps.get_llm),
) -> StreamingResponse:
    """基于错题生成变式练习（LLM，SSE 流式文本）。"""
    record = await _get_owned(db, mistake_id, user_id)

    async def stream() -> AsyncGenerator[str, None]:
        try:
            async for delta in mistake_service.stream_variations(llm, record):
                yield chat_service.sse_encode({"type": "delta", "content": delta})
            yield chat_service.sse_encode({"type": "done"})
        except Exception as exc:  # noqa: BLE001
            yield chat_service.sse_encode({"type": "error", "message": str(exc)[:300]})

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        }
    )
