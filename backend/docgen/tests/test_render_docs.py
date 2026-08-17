"""教案 / 试卷 / 报告渲染器测试：生成真实文件并读回校验。"""

from __future__ import annotations

from pathlib import Path

import docx
import pytest

from app.schemas import (
    ExamQuestion,
    LessonPlanContent,
    ProcedureStep,
    RenderExamPaperRequest,
    RenderLessonPlanRequest,
    RenderReportRequest,
    TrendPoint,
)
from app.services.render_exam_paper import render_exam_paper
from app.services.render_lesson_plan import render_lesson_plan
from app.services.render_report import render_report


def _lesson_plan_request(template_type: str) -> RenderLessonPlanRequest:
    return RenderLessonPlanRequest(
        title="分数的加法 教案",
        course_name="数学",
        grade="三年级",
        subject="数学",
        duration="45分钟",
        template_type=template_type,  # type: ignore[arg-type]
        content=LessonPlanContent(
            objectives=["理解同分母分数加法的算理", "能正确计算同分母分数加法"],
            key_points=["同分母分数加法法则"],
            difficulties=["算理理解"],
            procedure=[
                ProcedureStep(stage="导入", duration="5分钟", teacher_activity="创设分蛋糕情境", student_activity="观察思考", design_intent="激发兴趣"),
                ProcedureStep(stage="新授", duration="20分钟", teacher_activity="讲解算理", student_activity="动手操作", design_intent="理解算理"),
            ],
            board_design="1/4 + 2/4 = 3/4",
            homework="课本第 56 页第 1~3 题",
            reflection="",
        ),
    )


@pytest.mark.parametrize("template_type", ["standard", "new_standard", "simple"])
def test_render_lesson_plan(tmp_path: Path, template_type: str) -> None:
    data = render_lesson_plan(_lesson_plan_request(template_type))
    out = tmp_path / f"plan-{template_type}.docx"
    out.write_bytes(data)
    assert out.stat().st_size > 0

    doc = docx.Document(str(out))
    texts = [p.text for p in doc.paragraphs if p.text.strip()]
    assert len(texts) >= 8
    assert any("分数的加法" in t for t in texts)
    assert any("教学目标" in t or "核心素养目标" in t for t in texts)
    if template_type == "simple":
        assert len(doc.tables) == 1  # 仅课程信息表
    else:
        assert len(doc.tables) == 2  # 课程信息表 + 教学过程表
        proc = doc.tables[1]
        assert proc.rows[0].cells[0].text == "环节"
        assert len(proc.rows) == 3  # 表头 + 2 个环节
    # 页眉校验
    assert "数学" in doc.sections[0].header.paragraphs[0].text


def test_render_exam_paper_student_and_teacher(tmp_path: Path) -> None:
    request = RenderExamPaperRequest(
        title="三年级数学期中测试",
        grade="三年级",
        subject="数学",
        duration_minutes=90,
        total_score=100,
        questions=[
            ExamQuestion(number=1, type="choice", question="3+5=?", options=["6", "7", "8", "9"], answer="C", analysis="3+5=8", knowledge_tag="加法", score=5),
            ExamQuestion(number=2, type="blank", question="7x=21，x=__", answer="3", analysis="两边同除以 7", knowledge_tag="方程", score=5),
            ExamQuestion(number=3, type="essay", question="小明有 12 个苹果，吃了 3 个，还剩几个？", answer="9 个", analysis="12-3=9", knowledge_tag="减法", score=10),
        ],
    )
    student, teacher = render_exam_paper(request)
    s_path = tmp_path / "student.docx"
    t_path = tmp_path / "teacher.docx"
    s_path.write_bytes(student)
    t_path.write_bytes(teacher)
    assert s_path.stat().st_size > 0 and t_path.stat().st_size > 0

    s_doc = docx.Document(str(s_path))
    s_text = "\n".join(p.text for p in s_doc.paragraphs)
    assert "三年级数学期中测试" in s_text
    assert "3+5=?" in s_text and "A. 6" in s_text
    # 学生卷：无答案、有答题区下划线
    assert "【答案】" not in s_text
    assert "____" in s_text

    t_doc = docx.Document(str(t_path))
    t_text = "\n".join(p.text for p in t_doc.paragraphs)
    assert "【答案】" in t_text and "【解析】" in t_text
    assert "两边同除以 7" in t_text
    assert "教师卷" in t_text
    # 三种题型分组标题齐全
    assert "选择题" in t_text and "填空题" in t_text and "解答题" in t_text


def test_render_report(tmp_path: Path) -> None:
    request = RenderReportRequest(
        title="张三 3 月学情报告",
        heat_map_data={"分数加法": 0.9, "分数乘法": 0.45, "通分": 0.6},
        trend_data=[TrendPoint(label="第1周", score=78), TrendPoint(label="第2周", score=85)],
        weak_points=["分数乘法"],
        suggestions=["每天练习 10 道分数乘法口算"],
    )
    data = render_report(request)
    out = tmp_path / "report.pdf"
    out.write_bytes(data)
    assert out.stat().st_size > 500
    assert data.startswith(b"%PDF")
    assert b"STSong-Light" in data or b"STSong" in data  # CID 中文字体已嵌入引用
