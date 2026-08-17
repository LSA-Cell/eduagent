"""内容生成 JSON Schema 校验：PPT 大纲 / 教案 / 试卷 / 去重与质量检查。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.content_gen import (
    ExamPaperContent,
    ExamQuestion,
    FlashcardSpec,
    LessonPlanContent,
    PlanDay,
    PresentationOutline,
    dedup_questions,
    exam_coverage,
    validate_exam_questions,
)


def _slide(number: int, title: str = "标题") -> dict:
    return {"slide_number": number, "title": title,
            "content_points": ["要点1", "要点2"], "notes": "备注"}


class TestPresentationOutline:
    def test_valid(self) -> None:
        outline = PresentationOutline.model_validate({"slides": [_slide(1), _slide(2)]})
        assert len(outline.slides) == 2
        assert outline.slides[0].notes == "备注"

    def test_duplicate_slide_number_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PresentationOutline.model_validate({"slides": [_slide(1), _slide(1)]})

    def test_empty_slides_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PresentationOutline.model_validate({"slides": []})

    def test_empty_content_points_rejected(self) -> None:
        bad = _slide(1)
        bad["content_points"] = []
        with pytest.raises(ValidationError):
            PresentationOutline.model_validate({"slides": [bad]})


class TestLessonPlanContent:
    def _valid(self) -> dict:
        return {
            "objectives": ["掌握配方法"],
            "key_points": ["配方步骤"],
            "difficulties": ["符号处理"],
            "procedure": [
                {"stage": "导入", "duration_minutes": 5, "activities": "回顾一元二次方程"},
                {"stage": "新授", "duration_minutes": 30, "activities": "讲解配方法"},
                {"stage": "小结", "duration_minutes": 10, "activities": "归纳步骤"},
            ],
            "board_design": "配方法三步",
            "homework": "课后练习 1-3",
            "reflection": "关注符号错误",
        }

    def test_valid(self) -> None:
        content = LessonPlanContent.model_validate(self._valid())
        assert [p.stage for p in content.procedure] == ["导入", "新授", "小结"]

    def test_procedure_required(self) -> None:
        data = self._valid()
        data["procedure"] = []
        with pytest.raises(ValidationError):
            LessonPlanContent.model_validate(data)

    def test_objectives_required(self) -> None:
        data = self._valid()
        data["objectives"] = []
        with pytest.raises(ValidationError):
            LessonPlanContent.model_validate(data)


class TestExamPaper:
    def _question(self, number: int = 1, qtype: str = "single_choice", **kw) -> dict:
        base = {"number": number, "type": qtype, "question": f"题目{number}",
                "answer": "A", "score": 5}
        if qtype in {"single_choice", "multiple_choice"}:
            base["options"] = ["A. 1", "B. 2"]
        base.update(kw)
        return base

    def test_valid(self) -> None:
        paper = ExamPaperContent.model_validate({
            "questions": [self._question(1), self._question(2, "fill_blank", options=[])]
        })
        assert len(paper.questions) == 2

    def test_choice_needs_two_options(self) -> None:
        with pytest.raises(ValidationError):
            ExamQuestion.model_validate(self._question(1, options=["A. 仅一个"]))

    def test_score_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ExamQuestion.model_validate(self._question(1, score=0))

    def test_answer_required(self) -> None:
        with pytest.raises(ValidationError):
            ExamQuestion.model_validate(self._question(1, answer=""))

    def test_dedup_questions_resequences_numbers(self) -> None:
        questions = [
            ExamQuestion.model_validate(self._question(1, question="求 x+1=2 的解")),
            ExamQuestion.model_validate(self._question(2, question=" 求x+1=2的解 ")),  # 归一化后重复
            ExamQuestion.model_validate(self._question(3, question="求 2x=4 的解")),
        ]
        kept, removed = dedup_questions(questions)
        assert len(kept) == 2 and len(removed) == 1
        assert [q.number for q in kept] == [1, 2]  # 保留题重排题号

    def test_validate_exam_questions(self) -> None:
        dup = ExamQuestion.model_validate(self._question(1, question="同上"))
        questions = [dup, ExamQuestion.model_validate(self._question(2, question="同上"))]
        issues = validate_exam_questions(questions, expected_count=3)
        assert any("重复" in i for i in issues)
        assert any("题数不符" in i for i in issues)
        assert validate_exam_questions([dup], expected_count=1) == []

    def test_exam_coverage(self) -> None:
        questions = [
            ExamQuestion.model_validate(self._question(1, knowledge_tag="方程")),
            ExamQuestion.model_validate(self._question(2, "calculation", knowledge_tag="方程")),
        ]
        coverage = exam_coverage(questions)
        assert coverage["knowledge_tags"] == {"方程": 2}
        assert coverage["question_types"] == {"single_choice": 1, "calculation": 1}
        assert coverage["total_score"] == 10


class TestPlanAndFlashcardSpec:
    def test_plan_day(self) -> None:
        day = PlanDay.model_validate({"day": 1, "task": "复习有理数", "duration": 60})
        assert day.duration == 60
        with pytest.raises(ValidationError):
            PlanDay.model_validate({"day": 0, "task": "x", "duration": 60})
        with pytest.raises(ValidationError):
            PlanDay.model_validate({"day": 1, "task": "", "duration": 60})

    def test_flashcard_spec(self) -> None:
        spec = FlashcardSpec.model_validate({"front": "Q", "back": "A"})
        assert spec.front == "Q"
        with pytest.raises(ValidationError):
            FlashcardSpec.model_validate({"front": "", "back": "A"})
