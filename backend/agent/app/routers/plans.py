"""学习计划路由：生成（LLM 排程，Celery 异步）/ 列表 / 详情 / 打卡。

路径前缀 /study-plans，与 BFF 业务转发路径一致。
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eduagent_shared.models import StudyPlan

from app import deps
from app.schemas import PlanCheckinRequest, PlanCreateRequest, TaskAcceptedResponse
from app.services import plan_service
from app.tasks import gen_tasks

router = APIRouter(
    prefix="/study-plans",
    tags=["plans"],
    dependencies=[Depends(deps.require_internal_auth)],
)


@router.post("", status_code=202)
async def create_plan(
    req: PlanCreateRequest,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> TaskAcceptedResponse:
    """创建学习计划：建行后派 Celery 任务生成 generated_schedule，轮询详情接口。"""
    plan = await plan_service.create_plan(db, user_id, req)
    await db.commit()
    result = gen_tasks.generate_plan_schedule.delay(str(plan.id))
    return TaskAcceptedResponse(
        id=str(plan.id), task_id=result.id, poll_url=f"/study-plans/{plan.id}"
    )


@router.get("")
async def list_plans(
    status: Optional[str] = Query(default=None, description="active/completed/archived"),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """计划列表（按创建时间倒序）。"""
    stmt = select(StudyPlan).where(StudyPlan.user_id == user_id)
    if status:
        stmt = stmt.where(StudyPlan.status == status)
    stmt = stmt.order_by(StudyPlan.created_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return {"items": [plan_service.plan_to_dict(p) for p in rows]}


@router.get("/{plan_id}")
async def get_plan(
    plan_id: str,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """计划详情（生成任务轮询此接口，status=active 时 schedule 就绪）。"""
    plan = await db.get(StudyPlan, plan_id)
    if plan is None or str(plan.user_id) != str(user_id):
        raise HTTPException(status_code=404, detail="plan not found")
    return plan_service.plan_to_dict(plan)


@router.post("/{plan_id}/checkin")
async def checkin_plan(
    plan_id: str,
    req: PlanCheckinRequest,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """任务完成打卡：把 day 计入 completed_days 并刷新进度（幂等）。"""
    plan = await plan_service.checkin(db, plan_id, user_id, day=req.day, note=req.note)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    await db.commit()
    return plan_service.plan_to_dict(plan)
