"""试卷渲染器：同一份题目生成学生卷（无答案，留答题区）与教师卷（含答案解析）两份 DOCX。"""

from __future__ import annotations

import io

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH

from app.schemas import ExamQuestion, RenderExamPaperRequest

_TYPE_TITLES = {"choice": "选择题", "blank": "填空题", "essay": "解答题"}
_TYPE_ORDER = ["choice", "blank", "essay"]
_OPTION_LABELS = "ABCDEFGH"

# 学生卷答题区行数
_ANSWER_LINES = {"choice": 0, "blank": 1, "essay": 6}


def render_exam_paper(request: RenderExamPaperRequest) -> tuple[bytes, bytes]:
    """返回 (学生卷字节, 教师卷字节)。"""
    return _render(request, for_teacher=False), _render(request, for_teacher=True)


def _render(request: RenderExamPaperRequest, *, for_teacher: bool) -> bytes:
    doc = docx.Document()
    _add_paper_header(doc, request, for_teacher)

    grouped: dict[str, list[ExamQuestion]] = {t: [] for t in _TYPE_ORDER}
    for q in request.questions:
        grouped[q.type].append(q)

    section_idx = 0
    for qtype in _TYPE_ORDER:
        questions = sorted(grouped.get(qtype, []), key=lambda q: q.number)
        if not questions:
            continue
        section_idx += 1
        total = sum(q.score for q in questions)
        cn = "一二三四五六"[section_idx - 1]
        doc.add_heading(f"{cn}、{_TYPE_TITLES[qtype]}（共 {total:g} 分）", level=1)
        for q in questions:
            _add_question(doc, q, for_teacher=for_teacher)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _add_paper_header(doc: docx.Document, request: RenderExamPaperRequest, for_teacher: bool) -> None:
    """卷头：标题 + 考试信息行 + 装订线提示。"""
    title = doc.add_heading(request.title, level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    info = doc.add_paragraph(
        f"年级：{request.grade}    学科：{request.subject}    "
        f"时长：{request.duration_minutes} 分钟    总分：{request.total_score:g} 分"
    )
    info.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if for_teacher:
        marker = doc.add_paragraph("【教师卷 · 含答案与解析】")
        marker.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in marker.runs:
            run.bold = True
    else:
        hint = doc.add_paragraph("姓名：__________    班级：__________    考号：__________")
        hint.alignment = WD_ALIGN_PARAGRAPH.CENTER


def _add_question(doc: docx.Document, q: ExamQuestion, *, for_teacher: bool) -> None:
    p = doc.add_paragraph()
    p.add_run(f"{q.number}. ").bold = True
    p.add_run(f"（{q.score:g} 分）{q.question}")

    if q.type == "choice":
        for i, opt in enumerate(q.options):
            label = _OPTION_LABELS[i] if i < len(_OPTION_LABELS) else str(i + 1)
            doc.add_paragraph(f"{label}. {opt}", style="List Paragraph")

    if not for_teacher:
        # 学生卷：留答题区
        for _ in range(_ANSWER_LINES.get(q.type, 1)):
            doc.add_paragraph("_" * 60)
    else:
        # 教师卷：答案 / 解析 / 知识点
        ans = doc.add_paragraph()
        ans.add_run("【答案】").bold = True
        ans.add_run(q.answer or "（未提供）")
        if q.analysis:
            ana = doc.add_paragraph()
            ana.add_run("【解析】").bold = True
            ana.add_run(q.analysis)
        if q.knowledge_tag:
            tag = doc.add_paragraph()
            tag.add_run("【知识点】").bold = True
            tag.add_run(q.knowledge_tag)
    doc.add_paragraph()
