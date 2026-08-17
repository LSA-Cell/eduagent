"""教师内容生成引擎：LLM 产出结构化 JSON → 校验 → 落库 → docgen 渲染。

所有生成主体都是 `run_*_generation(session, llm, docgen, record)` 纯异步函数，
由 Celery 任务调用（同步接口只负责建行 + 派任务，绝不阻塞等 LLM/docgen）。

shared.models 与 init.sql 存在命名差异，字段映射约定（集中在本文件，便于后续对齐）：
- Presentation: topic→title, style→theme, pages→slide_count, file_url→object_key；
  thumbnail_urls/错误信息暂存 outline 字典（"_thumbnails"/"_error" 保留键）。
- LessonPlan: course_name→title, grade→grade_level, duration→duration_minutes；
  template_type 与渲染产物地址暂存 content["_render"]。
- ExamPaper: grade→grade_level；difficulty/题型/双卷地址/质量报告暂存 answer_key 字典。
- LearningAnalysis: class_name/data_source/报告/文件地址/状态 暂存 metrics 字典
  （report JSON 本体存 metrics["report"]，suggestions 同步写入 suggestions 列）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from eduagent_shared.models import ExamPaper, LearningAnalysis, LessonPlan, Presentation

from app import prompts
from app.services.docgen_client import DocgenClient
from app.services.llm_client import LLMClient


class ContentGenError(RuntimeError):
    """内容生成失败（LLM 输出非法 / 渲染失败等）。"""


# ---------------------------------------------------------------------------
# JSON Schema（Pydantic v2）
# ---------------------------------------------------------------------------
class SlideOutline(BaseModel):
    """PPT 单页大纲。"""

    slide_number: int = Field(ge=1)
    title: str = Field(min_length=1)
    content_points: list[str] = Field(min_length=1)
    notes: str = ""


class PresentationOutline(BaseModel):
    """PPT 大纲：{slides: [...]}"""

    slides: list[SlideOutline] = Field(min_length=1)

    @field_validator("slides")
    @classmethod
    def _slide_numbers_unique(cls, slides: list[SlideOutline]) -> list[SlideOutline]:
        numbers = [s.slide_number for s in slides]
        if len(numbers) != len(set(numbers)):
            raise ValueError("slide_number 重复")
        return slides


class LessonPlanProcedure(BaseModel):
    stage: str = Field(min_length=1)
    duration_minutes: int = Field(ge=0)
    activities: str = ""


class LessonPlanContent(BaseModel):
    """教案内容 JSON。"""

    objectives: list[str] = Field(min_length=1)
    key_points: list[str] = Field(default_factory=list)
    difficulties: list[str] = Field(default_factory=list)
    procedure: list[LessonPlanProcedure] = Field(min_length=1)
    board_design: str = ""
    homework: str = ""
    reflection: str = ""


QUESTION_TYPES = {
    "single_choice", "multiple_choice", "fill_blank",
    "true_false", "short_answer", "calculation",
}


class ExamQuestion(BaseModel):
    """试卷单题。"""

    number: int = Field(ge=1)
    type: str = Field(min_length=1)
    question: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    answer: str = Field(min_length=1)
    analysis: str = ""
    knowledge_tag: str = ""
    score: float = Field(gt=0)

    @field_validator("options")
    @classmethod
    def _choice_needs_options(cls, options: list[str], info: Any) -> list[str]:
        qtype = (info.data or {}).get("type", "")
        if qtype in {"single_choice", "multiple_choice"} and len(options) < 2:
            raise ValueError("选择题至少需要 2 个选项")
        return options


class ExamPaperContent(BaseModel):
    """试卷内容 JSON：{questions: [...]}"""

    questions: list[ExamQuestion] = Field(min_length=1)


class AnalysisReport(BaseModel):
    """学情分析报告 JSON。"""

    heat_map_data: list[dict[str, Any]] = Field(default_factory=list)
    trend_data: list[dict[str, Any]] = Field(default_factory=list)
    weak_points: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    summary: str = ""


class PlanDay(BaseModel):
    day: int = Field(ge=1)
    task: str = Field(min_length=1)
    duration: int = Field(ge=1, description="分钟")


class FlashcardSpec(BaseModel):
    front: str = Field(min_length=1)
    back: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# 试卷质量校验：去重 / 难度分布 / 知识点覆盖
# ---------------------------------------------------------------------------
def normalize_stem(question: str) -> str:
    """题干归一化（去空白小写），用于重复检测。"""
    return re.sub(r"\s+", "", question).lower()


def dedup_questions(questions: list[ExamQuestion]) -> tuple[list[ExamQuestion], list[ExamQuestion]]:
    """按归一化题干去重，返回 (保留, 被移除)；保留题重排题号保持连续。"""
    seen: set[str] = set()
    kept: list[ExamQuestion] = []
    removed: list[ExamQuestion] = []
    for q in questions:
        key = normalize_stem(q.question)
        if key in seen:
            removed.append(q)
            continue
        seen.add(key)
        kept.append(q)
    for i, q in enumerate(kept, start=1):
        q.number = i
    return kept, removed


def exam_coverage(questions: list[ExamQuestion]) -> dict[str, Any]:
    """统计知识点覆盖与题型分布（写入试卷 answer_key.meta）。"""
    tags: dict[str, int] = {}
    types: dict[str, int] = {}
    for q in questions:
        tag = q.knowledge_tag or "未标注"
        tags[tag] = tags.get(tag, 0) + 1
        types[q.type] = types.get(q.type, 0) + 1
    return {"knowledge_tags": tags, "question_types": types,
            "total_score": sum(q.score for q in questions)}


def validate_exam_questions(
    questions: list[ExamQuestion], expected_count: Optional[int] = None
) -> list[str]:
    """试卷质量校验，返回问题列表（空列表 = 通过）：重复题/题数/选项/答案。"""
    issues: list[str] = []
    stems: dict[str, int] = {}
    for q in questions:
        key = normalize_stem(q.question)
        stems[key] = stems.get(key, 0) + 1
    dup = [s for s, n in stems.items() if n > 1]
    if dup:
        issues.append(f"存在重复题目 {len(dup)} 处")
    if expected_count is not None and len(questions) != expected_count:
        issues.append(f"题数不符：期望 {expected_count}，实际 {len(questions)}")
    for q in questions:
        if q.type in {"single_choice", "multiple_choice"} and len(q.options) < 2:
            issues.append(f"第 {q.number} 题选择题选项不足")
        if not q.answer.strip():
            issues.append(f"第 {q.number} 题缺少答案")
    return issues


# ---------------------------------------------------------------------------
# 记录创建（同步接口调用：建行 + 返回 id，生成交给 Celery）
# ---------------------------------------------------------------------------
async def create_presentation(session: Any, user_id: str, req: Any) -> Any:
    record = Presentation(
        user_id=user_id, title=req.topic, subject=req.subject,
        grade_level=req.grade, theme=req.style, slide_count=req.pages,
        outline={"slides": [], "topic": req.topic, "_thumbnails": []},
        status="generating",
    )
    session.add(record)
    await session.flush()
    return record


async def create_lesson_plan(session: Any, user_id: str, req: Any) -> Any:
    record = LessonPlan(
        user_id=user_id, title=req.course_name, subject=req.subject if hasattr(req, "subject") else None,
        grade_level=req.grade, duration_minutes=req.duration,
        objectives=list(req.objectives),
        content={"_render": {"template_type": req.template_type}},
        status="generating",
    )
    session.add(record)
    await session.flush()
    return record


async def create_exam_paper(session: Any, user_id: str, req: Any) -> Any:
    record = ExamPaper(
        user_id=user_id, title=f"{req.grade}{req.subject}试卷",
        subject=req.subject, grade_level=req.grade,
        questions=[],
        answer_key={
            "difficulty": req.difficulty,
            "question_types": list(req.question_types),
            "expected_count": req.count,
        },
        status="generating",
    )
    session.add(record)
    await session.flush()
    return record


async def create_analysis(session: Any, user_id: str, req: Any) -> Any:
    record = LearningAnalysis(
        user_id=user_id, analysis_type=req.analysis_type,
        metrics={
            "class_name": req.class_name,
            "data_source": req.data_source,
            "status": "generating",
        },
        suggestions=[],
    )
    session.add(record)
    await session.flush()
    return record


# ---------------------------------------------------------------------------
# 生成主体（Celery 任务调用）：LLM → 校验 → 更新记录 → docgen 渲染
# ---------------------------------------------------------------------------
def _safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


async def run_ppt_generation(
    session: Any, llm: LLMClient, docgen: DocgenClient, record: Any
) -> dict[str, Any]:
    """PPT：LLM 大纲 → presentations.outline → docgen 渲染 → 回写文件地址。"""
    outline_store = dict(record.outline or {})
    try:
        raw = await llm.chat_json(
            "你是资深教研员，输出严格 JSON。",
            prompts.PPT_OUTLINE_PROMPT.format(
                grade=record.grade_level or "", subject=record.subject or "",
                topic=outline_store.get("topic") or record.title,
                style=record.theme or "default", pages=record.slide_count or 10,
            ),
        )
        outline = PresentationOutline.model_validate(raw)
        result = await docgen.render_ppt({
            "topic": outline_store.get("topic") or record.title,
            "grade": record.grade_level, "subject": record.subject,
            "style": record.theme, "slides": [s.model_dump() for s in outline.slides],
        })
        outline_store["slides"] = [s.model_dump() for s in outline.slides]
        outline_store["_thumbnails"] = result.get("thumbnail_urls", [])
        outline_store.pop("_error", None)
        record.outline = outline_store
        record.object_key = result["file_url"]  # file_url → object_key 列
        record.status = "ready"
    except Exception as exc:  # noqa: BLE001 - 失败落库供轮询
        outline_store["_error"] = str(exc)[:500]
        record.outline = outline_store
        record.status = "failed"
    await session.flush()
    return {"id": str(getattr(record, "id", "")), "status": record.status}


async def run_lesson_plan_generation(
    session: Any, llm: LLMClient, docgen: DocgenClient, record: Any
) -> dict[str, Any]:
    """教案：LLM JSON → lesson_plans.content → docgen 渲染 DOCX。"""
    content_store = dict(record.content or {})
    render_meta = dict(content_store.get("_render") or {})
    try:
        raw = await llm.chat_json(
            "你是资深教研员，输出严格 JSON。",
            prompts.LESSON_PLAN_PROMPT.format(
                grade=record.grade_level or "", course_name=record.title,
                duration=record.duration_minutes or 45,
                template_type=render_meta.get("template_type", "standard"),
                objectives="；".join(record.objectives or []) or "（无，自行设计）",
            ),
        )
        content = LessonPlanContent.model_validate(raw)
        result = await docgen.render_lesson_plan({
            "course_name": record.title, "grade": record.grade_level,
            "duration": record.duration_minutes,
            "template_type": render_meta.get("template_type", "standard"),
            "content": content.model_dump(),
        })
        render_meta["file_url"] = result["file_url"]
        render_meta["thumbnail_urls"] = result.get("thumbnail_urls", [])
        content_store.update(content.model_dump())
        content_store["_render"] = render_meta
        content_store.pop("_error", None)
        record.content = content_store
        record.status = "completed"
    except Exception as exc:  # noqa: BLE001
        content_store["_error"] = str(exc)[:500]
        content_store["_render"] = render_meta
        record.content = content_store
        record.status = "failed"
    await session.flush()
    return {"id": str(getattr(record, "id", "")), "status": record.status}


async def run_exam_paper_generation(
    session: Any, llm: LLMClient, docgen: DocgenClient, record: Any
) -> dict[str, Any]:
    """试卷：LLM 题目 JSON → 去重/分布校验 → exam_papers.questions → docgen 双卷渲染。"""
    meta = dict(record.answer_key or {})
    try:
        raw = await llm.chat_json(
            "你是资深命题教师，输出严格 JSON。",
            prompts.EXAM_PAPER_PROMPT.format(
                grade=record.grade_level or "", subject=record.subject or "",
                question_types="、".join(meta.get("question_types") or []),
                difficulty=meta.get("difficulty", "medium"),
                count=meta.get("expected_count", 10),
            ),
            temperature=0.5,
        )
        content = ExamPaperContent.model_validate(raw)
        questions, removed = dedup_questions(list(content.questions))
        issues = validate_exam_questions(questions)
        record.questions = [q.model_dump() for q in questions]
        record.total_score = int(sum(q.score for q in questions))
        render_result = await docgen.render_exam_paper({
            "grade": record.grade_level, "subject": record.subject,
            "questions": record.questions,
        })
        meta.update({
            "coverage": exam_coverage(questions),
            "dedup_removed": len(removed),
            "quality_issues": issues,
            "student_file_url": render_result["file_url"],
            # 教师卷（含答案解析）；docgen 约定字段，缺失时与学生卷同址
            "teacher_file_url": render_result.get("teacher_file_url", render_result["file_url"]),
            "thumbnail_urls": render_result.get("thumbnail_urls", []),
        })
        meta.pop("_error", None)
        record.answer_key = meta
        record.status = "completed"
    except Exception as exc:  # noqa: BLE001
        meta["_error"] = str(exc)[:500]
        record.answer_key = meta
        record.status = "failed"
    await session.flush()
    return {"id": str(getattr(record, "id", "")), "status": record.status}


async def run_analysis_generation(
    session: Any,
    llm: LLMClient,
    docgen: DocgenClient,
    record: Any,
    stats: dict[str, Any],
) -> dict[str, Any]:
    """学情分析：聚合统计 → LLM 报告 JSON → learning_analyses.metrics → docgen PDF。"""
    metrics = dict(record.metrics or {})
    try:
        raw = await llm.chat_json(
            "你是数据分析师兼教研专家，输出严格 JSON。",
            prompts.ANALYSIS_REPORT_PROMPT.format(
                class_name=metrics.get("class_name", ""),
                analysis_type=record.analysis_type,
                stats=_safe_json(stats),
            ),
        )
        report = AnalysisReport.model_validate(raw)
        result = await docgen.render_analysis({
            "class_name": metrics.get("class_name", ""),
            "analysis_type": record.analysis_type,
            "report": report.model_dump(), "stats": stats,
        })
        metrics["report"] = report.model_dump()
        metrics["file_url"] = result["file_url"]
        metrics["status"] = "completed"
        metrics.pop("_error", None)
        record.metrics = metrics
        record.summary = report.summary or "；".join(report.suggestions[:2])
        record.suggestions = report.suggestions
    except Exception as exc:  # noqa: BLE001
        metrics["status"] = "failed"
        metrics["_error"] = str(exc)[:500]
        record.metrics = metrics
    await session.flush()
    return {"id": str(getattr(record, "id", "")), "status": metrics.get("status", "failed")}


# ---------------------------------------------------------------------------
# 记录 → API DTO（屏蔽底层字段映射，BFF 只见规格字段）
# ---------------------------------------------------------------------------
def presentation_to_dict(record: Any) -> dict[str, Any]:
    outline = dict(record.outline or {})
    return {
        "id": str(record.id), "topic": outline.get("topic") or record.title,
        "grade": record.grade_level, "subject": record.subject,
        "style": record.theme, "pages": record.slide_count,
        "slides": outline.get("slides") or [],
        "file_url": record.object_key,
        "thumbnail_urls": outline.get("_thumbnails") or [],
        "status": record.status, "error": outline.get("_error"),
        "created_at": _iso(record.created_at),
    }


def lesson_plan_to_dict(record: Any) -> dict[str, Any]:
    content = dict(record.content or {})
    render = dict(content.pop("_render", {}) or {})
    content.pop("_error", None)
    return {
        "id": str(record.id), "course_name": record.title,
        "grade": record.grade_level, "subject": record.subject,
        "duration": record.duration_minutes,
        "template_type": render.get("template_type", "standard"),
        "objectives": record.objectives or [], "content": content,
        "file_url": render.get("file_url"),
        "thumbnail_urls": render.get("thumbnail_urls") or [],
        "status": record.status,
        "error": (record.content or {}).get("_error"),
        "created_at": _iso(record.created_at),
    }


def exam_paper_to_dict(record: Any) -> dict[str, Any]:
    meta = dict(record.answer_key or {})
    return {
        "id": str(record.id), "title": record.title,
        "grade": record.grade_level, "subject": record.subject,
        "difficulty": meta.get("difficulty"),
        "question_types": meta.get("question_types") or [],
        "count": len(record.questions or []),
        "total_score": record.total_score,
        "questions": record.questions or [],
        "coverage": meta.get("coverage"),
        "quality_issues": meta.get("quality_issues") or [],
        "student_file_url": meta.get("student_file_url"),
        "teacher_file_url": meta.get("teacher_file_url"),
        "status": record.status, "error": meta.get("_error"),
        "created_at": _iso(record.created_at),
    }


def analysis_to_dict(record: Any) -> dict[str, Any]:
    metrics = dict(record.metrics or {})
    return {
        "id": str(record.id), "class_name": metrics.get("class_name"),
        "analysis_type": record.analysis_type,
        "data_source": metrics.get("data_source") or {},
        "report": metrics.get("report"),
        "summary": record.summary, "suggestions": record.suggestions or [],
        "file_url": metrics.get("file_url"),
        "status": metrics.get("status", "generating"),
        "error": metrics.get("_error"),
        "created_at": _iso(record.created_at),
    }


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else None
