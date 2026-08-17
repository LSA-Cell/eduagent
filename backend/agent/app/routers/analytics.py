"""学情分析路由：知识点掌握度矩阵。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app import deps
from app.services import analytics_service

router = APIRouter(
    prefix="/analytics",
    tags=["analytics"],
    dependencies=[Depends(deps.require_internal_auth)],
)


@router.get("/knowledge-mastery")
async def knowledge_mastery(
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """知识点掌握度矩阵（按掌握度升序，最薄弱的排前面）。"""
    matrix = await analytics_service.knowledge_mastery(db, user_id)
    return {"user_id": user_id, "matrix": matrix}
