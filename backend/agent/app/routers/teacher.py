"""教师内容生成路由：PPT / 教案 / 试卷 / 学情分析。

统一模式：POST 创建记录（status=generating）→ 202 返回任务与轮询地址 →
Celery 任务完成 LLM 生成与 docgen 渲染 → GET 详情轮询 status/file_url 与预览 JSON。
学情分析同时挂在 /teacher/analyses 与 /teacher/learning-analyses（后者对齐 BFF 转发路径）。
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eduagent_shared.models import ExamPaper, LearningAnalysis, LessonPlan, Presentation

from app import deps
from app.schemas import (
    AnalysisCreateRequest,
    ExamPaperCreateRequest,
    LessonPlanCreateRequest,
    PresentationCreateRequest,
    TaskAcceptedResponse,
)
from app.services import content_gen
from app.tasks import gen_tasks

router = APIRouter(
    prefix="/teacher",
    tags=["teacher"],
    dependencies=[Depends(deps.require_internal_auth)],
)


async def _submit(
    db: AsyncSession,
    create_fn: Callable[..., Any],
    task: Any,
    req: Any,
    user_id: str,
    poll_url: str,
) -> TaskAcceptedResponse:
    """建行 → 提交事务 → 派 Celery 任务 → 202 响应。"""
    record = await create_fn(db, user_id, req)
    await db.commit()
    result = task.delay(str(record.id))
    return TaskAcceptedResponse(
        id=str(record.id), task_id=result.id, poll_url=poll_url.format(id=record.id)
    )


async def _list(
    db: AsyncSession, model: Any, to_dict: Callable[[Any], dict[str, Any]],
    user_id: str, limit: int,
) -> dict[str, Any]:
    stmt = (
        select(model)
        .where(model.user_id == user_id)
        .order_by(model.created_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {"items": [to_dict(r) for r in rows]}


async def _detail(
    db: AsyncSession, model: Any, to_dict: Callable[[Any], dict[str, Any]],
    item_id: str, user_id: str,
) -> dict[str, Any]:
    record = await db.get(model, item_id)
    if record is None or str(record.user_id) != str(user_id):
        raise HTTPException(status_code=404, detail="resource not found")
    return to_dict(record)


# ---------------------------------------------------------------------------
# PPT 课件
# ---------------------------------------------------------------------------
@router.post("/presentations", status_code=202)
async def create_presentation(
    req: PresentationCreateRequest,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> TaskAcceptedResponse:
    """创建 PPT 生成任务。"""
    return await _submit(
        db, content_gen.create_presentation, gen_tasks.generate_presentation,
        req, user_id, "/teacher/presentations/{id}",
    )


@router.get("/presentations")
async def list_presentations(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """PPT 列表。"""
    return await _list(db, Presentation, content_gen.presentation_to_dict, user_id, limit)


@router.get("/presentations/{item_id}")
async def get_presentation(
    item_id: str,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """PPT 详情：含 file_url、thumbnail_urls 与 slides 预览。"""
    return await _detail(db, Presentation, content_gen.presentation_to_dict, item_id, user_id)


# ---------------------------------------------------------------------------
# 教案
# ---------------------------------------------------------------------------
@router.post("/lesson-plans", status_code=202)
async def create_lesson_plan(
    req: LessonPlanCreateRequest,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> TaskAcceptedResponse:
    """创建教案生成任务。"""
    return await _submit(
        db, content_gen.create_lesson_plan, gen_tasks.generate_lesson_plan,
        req, user_id, "/teacher/lesson-plans/{id}",
    )


@router.get("/lesson-plans")
async def list_lesson_plans(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """教案列表。"""
    return await _list(db, LessonPlan, content_gen.lesson_plan_to_dict, user_id, limit)


@router.get("/lesson-plans/{item_id}")
async def get_lesson_plan(
    item_id: str,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """教案详情：含 file_url 与结构化 content 预览。"""
    return await _detail(db, LessonPlan, content_gen.lesson_plan_to_dict, item_id, user_id)


# ---------------------------------------------------------------------------
# 试卷
# ---------------------------------------------------------------------------
@router.post("/exam-papers", status_code=202)
async def create_exam_paper(
    req: ExamPaperCreateRequest,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> TaskAcceptedResponse:
    """创建试卷生成任务。"""
    return await _submit(
        db, content_gen.create_exam_paper, gen_tasks.generate_exam_paper,
        req, user_id, "/teacher/exam-papers/{id}",
    )


@router.get("/exam-papers")
async def list_exam_papers(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """试卷列表。"""
    return await _list(db, ExamPaper, content_gen.exam_paper_to_dict, user_id, limit)


@router.get("/exam-papers/{item_id}")
async def get_exam_paper(
    item_id: str,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """试卷详情：含学生卷/教师卷地址、题目 JSON 预览与质量报告。"""
    return await _detail(db, ExamPaper, content_gen.exam_paper_to_dict, item_id, user_id)


# ---------------------------------------------------------------------------
# 学情分析（双路径：/analyses 与 BFF 对齐的 /learning-analyses）
# ---------------------------------------------------------------------------
@router.post("/learning-analyses", status_code=202)
@router.post("/analyses", status_code=202)
async def create_analysis(
    req: AnalysisCreateRequest,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> TaskAcceptedResponse:
    """创建学情分析任务。"""
    return await _submit(
        db, content_gen.create_analysis, gen_tasks.generate_analysis,
        req, user_id, "/teacher/learning-analyses/{id}",
    )


@router.get("/learning-analyses")
@router.get("/analyses")
async def list_analyses(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """学情分析列表。"""
    return await _list(db, LearningAnalysis, content_gen.analysis_to_dict, user_id, limit)


@router.get("/learning-analyses/{item_id}")
@router.get("/analyses/{item_id}")
async def get_analysis(
    item_id: str,
    db: AsyncSession = Depends(deps.get_db),
    user_id: str = Depends(deps.get_user_id),
) -> dict[str, Any]:
    """学情分析详情：含 file_url 与报告 JSON 预览。"""
    return await _detail(db, LearningAnalysis, content_gen.analysis_to_dict, item_id, user_id)
