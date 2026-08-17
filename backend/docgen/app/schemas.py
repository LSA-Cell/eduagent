"""文档生成服务接口模型（Pydantic v2）。

本服务只排版渲染：入参均为调用方生成好的结构化内容。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------- PPT ----------


class SlideSpec(BaseModel):
    slide_number: int
    layout: Literal["title", "toc", "content", "summary"]
    title: str
    content_points: list[str] = Field(default_factory=list)
    notes: str | None = None


class RenderPptRequest(BaseModel):
    title: str
    style: Literal["minimal", "academic", "lively"] = "minimal"
    slides: list[SlideSpec] = Field(min_length=1)


class PptResult(BaseModel):
    file_url: str
    object_key: str
    slide_count: int


# ---------- 教案 ----------


class ProcedureStep(BaseModel):
    stage: str = Field(description="教学环节，如 导入/新授/巩固/小结")
    duration: str = Field(description="环节时长，如 5分钟")
    teacher_activity: str
    student_activity: str
    design_intent: str = ""


class LessonPlanContent(BaseModel):
    objectives: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    difficulties: list[str] = Field(default_factory=list)
    procedure: list[ProcedureStep] = Field(default_factory=list)
    board_design: str = ""
    homework: str = ""
    reflection: str = ""


class RenderLessonPlanRequest(BaseModel):
    title: str
    course_name: str
    grade: str
    subject: str
    duration: str = Field(description="课时长度，如 45分钟")
    template_type: Literal["standard", "new_standard", "simple"] = "standard"
    content: LessonPlanContent


class FileResult(BaseModel):
    file_url: str
    object_key: str


# ---------- 试卷 ----------


class ExamQuestion(BaseModel):
    number: int
    type: Literal["choice", "blank", "essay"]
    question: str
    options: list[str] = Field(default_factory=list, description="选择题选项")
    answer: str = ""
    analysis: str = ""
    knowledge_tag: str = ""
    score: float = 0


class RenderExamPaperRequest(BaseModel):
    title: str
    grade: str
    subject: str
    duration_minutes: int = Field(gt=0)
    total_score: float = Field(gt=0)
    questions: list[ExamQuestion] = Field(min_length=1)


class ExamPaperResult(BaseModel):
    student_file_url: str
    student_object_key: str
    teacher_file_url: str
    teacher_object_key: str


# ---------- 学情报告 ----------


class TrendPoint(BaseModel):
    label: str = Field(description="时间点标签，如 第1周 / 2024-03")
    score: float


class RenderReportRequest(BaseModel):
    title: str
    heat_map_data: dict[str, float] = Field(
        default_factory=dict, description="知识点 -> 掌握率(0~1)"
    )
    trend_data: list[TrendPoint] = Field(default_factory=list)
    weak_points: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


# ---------- 任务 ----------


class TaskCreated(BaseModel):
    task_id: str
    status: str = "pending"


class TaskStatus(BaseModel):
    task_id: str
    task_type: str
    status: str
    result: dict | None = None
    error: str | None = None
