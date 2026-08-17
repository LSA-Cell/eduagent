"""任务查询路由：GET /tasks/{task_id}。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import verify_internal_token
from app.schemas import TaskStatus
from app.tasks import get_task_store

router = APIRouter(dependencies=[Depends(verify_internal_token)])


@router.get("/tasks/{task_id}", response_model=TaskStatus)
async def get_task(task_id: str) -> TaskStatus:
    """查询渲染任务状态与结果。"""
    record = get_task_store().get(task_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    return TaskStatus(
        task_id=record.task_id,
        task_type=record.task_type,
        status=record.status,
        result=record.result,
        error=record.error,
    )
