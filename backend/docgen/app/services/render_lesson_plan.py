"""教案渲染器：python-docx 按新课标格式渲染（标题样式/表格/页眉页脚）。

template_type:
- standard：传统格式（教学目标/重难点/教学过程四列表格）
- new_standard：新课标格式（核心素养目标，教学过程含设计意图列）
- simple：简案（仅目标 + 流程要点，无表格）
"""

from __future__ import annotations

import io

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH

from app.schemas import RenderLessonPlanRequest


def render_lesson_plan(request: RenderLessonPlanRequest) -> bytes:
    """渲染教案 DOCX，返回文件字节。"""
    doc = docx.Document()
    _setup_header_footer(doc, request)

    # 标题
    title = doc.add_heading(request.title, level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    _add_info_table(doc, request)
    _add_objectives(doc, request)
    _add_key_difficulties(doc, request)
    _add_procedure(doc, request)
    _add_tail_sections(doc, request)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _setup_header_footer(doc: docx.Document, request: RenderLessonPlanRequest) -> None:
    """页眉：课程名；页脚：教案标识。"""
    section = doc.sections[0]
    header_para = section.header.paragraphs[0]
    header_para.text = f"{request.course_name} · {request.grade} · {request.subject}"
    header_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_para = section.footer.paragraphs[0]
    footer_para.text = "EduAgent 智能教案"
    footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER


def _add_info_table(doc: docx.Document, request: RenderLessonPlanRequest) -> None:
    """课程信息表：课程/年级/学科/课时。"""
    table = doc.add_table(rows=2, cols=4)
    table.style = "Table Grid"
    labels = ["课程名称", "年级", "学科", "课时"]
    values = [request.course_name, request.grade, request.subject, request.duration]
    for col, (label, value) in enumerate(zip(labels, values)):
        table.cell(0, col).text = label
        table.cell(1, col).text = value
    doc.add_paragraph()


def _add_objectives(doc: docx.Document, request: RenderLessonPlanRequest) -> None:
    c = request.content
    if not c.objectives:
        return
    heading = "核心素养目标" if request.template_type == "new_standard" else "教学目标"
    doc.add_heading(heading, level=1)
    for i, obj in enumerate(c.objectives, start=1):
        doc.add_paragraph(f"{i}. {obj}")


def _add_key_difficulties(doc: docx.Document, request: RenderLessonPlanRequest) -> None:
    c = request.content
    if c.key_points:
        doc.add_heading("教学重点", level=1)
        for i, kp in enumerate(c.key_points, start=1):
            doc.add_paragraph(f"{i}. {kp}")
    if c.difficulties:
        doc.add_heading("教学难点", level=1)
        for i, df in enumerate(c.difficulties, start=1):
            doc.add_paragraph(f"{i}. {df}")


def _add_procedure(doc: docx.Document, request: RenderLessonPlanRequest) -> None:
    c = request.content
    if not c.procedure:
        return
    doc.add_heading("教学过程", level=1)

    if request.template_type == "simple":
        # 简案：环节 + 活动概述，不用表格
        for step in c.procedure:
            p = doc.add_paragraph()
            p.add_run(f"【{step.stage}】（{step.duration}）").bold = True
            doc.add_paragraph(f"教师：{step.teacher_activity}")
            doc.add_paragraph(f"学生：{step.student_activity}")
        return

    # 标准/新课标：五列表格（新课标保留设计意图列，标准版合并简述）
    headers = ["环节", "时长", "教师活动", "学生活动", "设计意图"]
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for col, h in enumerate(headers):
        cell = table.cell(0, col)
        cell.text = h
        for run in cell.paragraphs[0].runs:
            run.bold = True
    for step in c.procedure:
        row = table.add_row()
        values = [
            step.stage,
            step.duration,
            step.teacher_activity,
            step.student_activity,
            step.design_intent or "—",
        ]
        for col, value in enumerate(values):
            row.cells[col].text = value
    doc.add_paragraph()


def _add_tail_sections(doc: docx.Document, request: RenderLessonPlanRequest) -> None:
    c = request.content
    for heading, text in (
        ("板书设计", c.board_design),
        ("作业布置", c.homework),
        ("教学反思", c.reflection),
    ):
        if text:
            doc.add_heading(heading, level=1)
            doc.add_paragraph(text)
