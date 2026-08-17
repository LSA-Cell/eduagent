"""渲染任务编排：后台执行 渲染 -> 上传 MinIO -> 更新任务状态。"""

from __future__ import annotations

import logging
import time

from app.schemas import (
    RenderExamPaperRequest,
    RenderLessonPlanRequest,
    RenderPptRequest,
    RenderReportRequest,
)
from app.services.render_exam_paper import render_exam_paper
from app.services.render_lesson_plan import render_lesson_plan
from app.services.render_ppt import render_ppt
from app.services.render_report import render_report
from app.storage import get_storage
from app.tasks import TaskRecord, TaskStore

logger = logging.getLogger(__name__)

_CONTENT_TYPES = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


def _object_key(prefix: str, task_id: str, ext: str) -> str:
    return f"{prefix}/{time.strftime('%Y/%m%d')}/{task_id}.{ext}"


def _run(store: TaskStore, record: TaskRecord, fn) -> None:  # type: ignore[no-untyped-def]
    """统一的后台执行骨架：状态流转 pending -> processing -> completed/failed。"""
    record.status = "processing"
    store.update(record)
    try:
        record.result = fn()
        record.status = "completed"
    except Exception as exc:  # noqa: BLE001 - 任何渲染失败都要落状态
        logger.exception("render task %s failed", record.task_id)
        record.status = "failed"
        record.error = str(exc)
    store.update(record)


def run_ppt_task(store: TaskStore, record: TaskRecord, request: RenderPptRequest) -> None:
    """PPT 渲染任务。"""

    def job() -> dict:
        data = render_ppt(request)
        key = _object_key("ppt", record.task_id, "pptx")
        url, key = get_storage().upload_and_sign(data, key, _CONTENT_TYPES["pptx"])
        return {"file_url": url, "object_key": key, "slide_count": len(request.slides)}

    _run(store, record, job)


def run_lesson_plan_task(store: TaskStore, record: TaskRecord, request: RenderLessonPlanRequest) -> None:
    """教案渲染任务（DOCX；如需 PDF 可在部署侧挂 LibreOffice 转换，本服务不内置）。"""

    def job() -> dict:
        data = render_lesson_plan(request)
        key = _object_key("lesson_plan", record.task_id, "docx")
        url, key = get_storage().upload_and_sign(data, key, _CONTENT_TYPES["docx"])
        return {"file_url": url, "object_key": key}

    _run(store, record, job)


def run_exam_paper_task(store: TaskStore, record: TaskRecord, request: RenderExamPaperRequest) -> None:
    """试卷渲染任务：学生卷 + 教师卷两份 DOCX。"""

    def job() -> dict:
        student, teacher = render_exam_paper(request)
        storage = get_storage()
        s_key = _object_key("exam", record.task_id + "-student", "docx")
        t_key = _object_key("exam", record.task_id + "-teacher", "docx")
        s_url, s_key = storage.upload_and_sign(student, s_key, _CONTENT_TYPES["docx"])
        t_url, t_key = storage.upload_and_sign(teacher, t_key, _CONTENT_TYPES["docx"])
        return {
            "student_file_url": s_url,
            "student_object_key": s_key,
            "teacher_file_url": t_url,
            "teacher_object_key": t_key,
        }

    _run(store, record, job)


def run_report_task(store: TaskStore, record: TaskRecord, request: RenderReportRequest) -> None:
    """学情报告渲染任务（PDF）。"""

    def job() -> dict:
        data = render_report(request)
        key = _object_key("report", record.task_id, "pdf")
        url, key = get_storage().upload_and_sign(data, key, _CONTENT_TYPES["pdf"])
        return {"file_url": url, "object_key": key}

    _run(store, record, job)
