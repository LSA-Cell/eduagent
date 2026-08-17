"""渲染路由：四个异步渲染接口，立即返回 task_id，后台执行。"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends

from app.deps import verify_internal_token
from app.schemas import (
    RenderExamPaperRequest,
    RenderLessonPlanRequest,
    RenderPptRequest,
    RenderReportRequest,
    TaskCreated,
)
from app.services import jobs
from app.tasks import get_task_store

router = APIRouter(dependencies=[Depends(verify_internal_token)])


@router.post("/render/ppt", response_model=TaskCreated, status_code=202)
async def render_ppt_route(
    payload: RenderPptRequest, background: BackgroundTasks
) -> TaskCreated:
    """渲染 PPTX 并上传 MinIO，异步执行。"""
    store = get_task_store()
    record = store.create("ppt")
    background.add_task(jobs.run_ppt_task, store, record, payload)
    return TaskCreated(task_id=record.task_id)


@router.post("/render/lesson_plan", response_model=TaskCreated, status_code=202)
async def render_lesson_plan_route(
    payload: RenderLessonPlanRequest, background: BackgroundTasks
) -> TaskCreated:
    """渲染教案 DOCX 并上传 MinIO，异步执行。"""
    store = get_task_store()
    record = store.create("lesson_plan")
    background.add_task(jobs.run_lesson_plan_task, store, record, payload)
    return TaskCreated(task_id=record.task_id)


@router.post("/render/exam_paper", response_model=TaskCreated, status_code=202)
async def render_exam_paper_route(
    payload: RenderExamPaperRequest, background: BackgroundTasks
) -> TaskCreated:
    """渲染学生卷 + 教师卷两份 DOCX 并上传 MinIO，异步执行。"""
    store = get_task_store()
    record = store.create("exam_paper")
    background.add_task(jobs.run_exam_paper_task, store, record, payload)
    return TaskCreated(task_id=record.task_id)


@router.post("/render/report", response_model=TaskCreated, status_code=202)
async def render_report_route(
    payload: RenderReportRequest, background: BackgroundTasks
) -> TaskCreated:
    """渲染学情报告 PDF 并上传 MinIO，异步执行。"""
    store = get_task_store()
    record = store.create("report")
    background.add_task(jobs.run_report_task, store, record, payload)
    return TaskCreated(task_id=record.task_id)
