"""内容生成任务：教师 PPT / 教案 / 试卷 / 学情分析与学习计划排程。

统一骨架：按记录 ID 取行 → content_gen 生成结构化 JSON → docgen 提交渲染并
轮询 → 回写 status / file_url / thumbnail_urls（细节均在 content_gen 的
run_*_generation 内，失败也会落库为 failed 供轮询）。
"""

from __future__ import annotations

from typing import Any, Optional

from eduagent_shared import db
from eduagent_shared.config import get_settings
from eduagent_shared.logging import get_logger
from eduagent_shared.models import (
    ExamPaper,
    LearningAnalysis,
    LessonPlan,
    Presentation,
    StudyPlan,
)

from app.services import analytics_service, content_gen, plan_service
from app.services.docgen_client import get_docgen_client
from app.services.llm_client import get_llm_client
from app.tasks.celery_app import celery_app, run_async

log = get_logger("agent.tasks.gen")


def _clients(llm: Any, docgen: Any) -> tuple[Any, Any]:
    """缺省构造真实客户端（推理网关 / docgen），测试可注入替身。"""
    settings = get_settings()
    return llm or get_llm_client(settings), docgen or get_docgen_client(settings)


async def run_generate_presentation(
    record_id: str, *, session_factory: Any = None, llm: Any = None, docgen: Any = None
) -> dict[str, Any]:
    """PPT：LLM 大纲 → presentations.outline → docgen 渲染 → 回写文件地址。"""
    factory = session_factory or db.get_session_factory()
    llm, docgen = _clients(llm, docgen)
    async with factory() as session:
        record = await session.get(Presentation, record_id)
        if record is None:
            return {"id": record_id, "status": "not_found"}
        result = await content_gen.run_ppt_generation(session, llm, docgen, record)
        await session.commit()
        log.info("presentation_generated", record_id=record_id, status=result["status"])
        return result


async def run_generate_lesson_plan(
    record_id: str, *, session_factory: Any = None, llm: Any = None, docgen: Any = None
) -> dict[str, Any]:
    """教案：LLM 结构化 JSON → lesson_plans.content → docgen 渲染 DOCX。"""
    factory = session_factory or db.get_session_factory()
    llm, docgen = _clients(llm, docgen)
    async with factory() as session:
        record = await session.get(LessonPlan, record_id)
        if record is None:
            return {"id": record_id, "status": "not_found"}
        result = await content_gen.run_lesson_plan_generation(session, llm, docgen, record)
        await session.commit()
        log.info("lesson_plan_generated", record_id=record_id, status=result["status"])
        return result


async def run_generate_exam_paper(
    record_id: str, *, session_factory: Any = None, llm: Any = None, docgen: Any = None
) -> dict[str, Any]:
    """试卷：LLM 题目 JSON → 去重/质量校验 → docgen 双卷渲染。"""
    factory = session_factory or db.get_session_factory()
    llm, docgen = _clients(llm, docgen)
    async with factory() as session:
        record = await session.get(ExamPaper, record_id)
        if record is None:
            return {"id": record_id, "status": "not_found"}
        result = await content_gen.run_exam_paper_generation(session, llm, docgen, record)
        await session.commit()
        log.info("exam_paper_generated", record_id=record_id, status=result["status"])
        return result


async def run_generate_analysis(
    record_id: str, *, session_factory: Any = None, llm: Any = None, docgen: Any = None
) -> dict[str, Any]:
    """学情分析：聚合错题统计 → LLM 报告 JSON → docgen 渲染 PDF。

    data_source 约定：{"student_ids": [...], "days": 30}（均可选，缺省分析创建者本人）。
    """
    factory = session_factory or db.get_session_factory()
    llm, docgen = _clients(llm, docgen)
    async with factory() as session:
        record = await session.get(LearningAnalysis, record_id)
        if record is None:
            return {"id": record_id, "status": "not_found"}
        data_source = dict((record.metrics or {}).get("data_source") or {})
        student_ids = [str(s) for s in data_source.get("student_ids") or [record.user_id]]
        days: Optional[int] = data_source.get("days")
        stats = await analytics_service.aggregate_mistake_stats(session, student_ids, days)
        result = await content_gen.run_analysis_generation(session, llm, docgen, record, stats)
        await session.commit()
        log.info("analysis_generated", record_id=record_id, status=result["status"])
        return result


async def run_generate_plan_schedule(
    plan_id: str, *, session_factory: Any = None, llm: Any = None
) -> dict[str, Any]:
    """学习计划排程：LLM 产出 [{day, task, duration}] 写入 plan_content.schedule。"""
    factory = session_factory or db.get_session_factory()
    settings = get_settings()
    llm = llm or get_llm_client(settings)
    async with factory() as session:
        plan = await session.get(StudyPlan, plan_id)
        if plan is None:
            return {"id": plan_id, "status": "not_found"}
        result = await plan_service.run_plan_generation(session, llm, plan)
        await session.commit()
        log.info("plan_schedule_generated", plan_id=plan_id, status=result["status"])
        return result


@celery_app.task(name="agent.gen.presentation")
def generate_presentation(record_id: str) -> dict[str, Any]:
    """PPT 生成（Celery 入口）。"""
    return run_async(run_generate_presentation(record_id))


@celery_app.task(name="agent.gen.lesson_plan")
def generate_lesson_plan(record_id: str) -> dict[str, Any]:
    """教案生成（Celery 入口）。"""
    return run_async(run_generate_lesson_plan(record_id))


@celery_app.task(name="agent.gen.exam_paper")
def generate_exam_paper(record_id: str) -> dict[str, Any]:
    """试卷生成（Celery 入口）。"""
    return run_async(run_generate_exam_paper(record_id))


@celery_app.task(name="agent.gen.analysis")
def generate_analysis(record_id: str) -> dict[str, Any]:
    """学情分析生成（Celery 入口）。"""
    return run_async(run_generate_analysis(record_id))


@celery_app.task(name="agent.gen.plan_schedule")
def generate_plan_schedule(plan_id: str) -> dict[str, Any]:
    """学习计划排程（Celery 入口）。"""
    return run_async(run_generate_plan_schedule(plan_id))
