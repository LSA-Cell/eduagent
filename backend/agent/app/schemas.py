"""API 请求/响应 Pydantic 模型。"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---------- 聊天 ----------
class ChatRequest(BaseModel):
    """POST /agent/chat 请求体。"""

    user_id: str
    message: str = Field(min_length=1)
    session_id: Optional[str] = None
    subject: Optional[str] = None
    model: Optional[str] = None
    enable_rag: bool = False
    enable_tools: bool = True


class ChatStreamRequest(ChatRequest):
    """POST /v1/chat/stream 请求体（编排层新 SSE 协议）。

    role/grade_level/is_minor/quota_tier 由 BFF 依据用户记录透传；
    confirm_token 为护栏 ask 确认后的重发放行令牌（见 POST /v1/guardrails/confirms）。
    """

    role: Literal["student", "teacher", "parent"] = "student"
    grade_level: Optional[str] = None
    is_minor: bool = False
    quota_tier: str = "free"
    confirm_token: Optional[str] = None


# ---------- 护栏确认 ----------
class GuardrailConfirmRequest(BaseModel):
    """POST /v1/guardrails/confirms 请求体。"""

    confirm_token: str = Field(min_length=8, max_length=64)
    approved: bool


# ---------- 错题 ----------
class MistakeReviewRequest(BaseModel):
    """复习打卡：记录本轮是否答对，用于掌握度统计。"""

    correct: bool = True


# ---------- 知识库 ----------
class DocumentUploadUrlRequest(BaseModel):
    """POST /kb/documents/upload-url 请求体（客户端直传 MinIO 前的预签名申请）。"""

    filename: str = Field(min_length=1, max_length=255)
    content_type: Optional[str] = Field(default=None, max_length=100)
    size: Optional[int] = Field(default=None, ge=0, description="文件字节数，用于上限校验")


class DocumentRegisterRequest(BaseModel):
    """文档上传登记（文件已由 BFF 传到 MinIO）。"""

    title: str = Field(min_length=1)
    subject: Optional[str] = None
    file_url: str = Field(min_length=1)
    file_type: Optional[str] = None  # pdf/docx/image 等，供多模态服务参考


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    subject: Optional[str] = None
    top_k: int = Field(default=5, ge=1, le=20)


# ---------- 教师内容生成 ----------
class PresentationCreateRequest(BaseModel):
    topic: str = Field(min_length=1)
    grade: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    style: str = "default"
    pages: int = Field(default=10, ge=1, le=50)


class LessonPlanCreateRequest(BaseModel):
    course_name: str = Field(min_length=1)
    grade: str = Field(min_length=1)
    duration: int = Field(default=45, ge=5, le=180, description="课时分钟数")
    objectives: list[str] = Field(default_factory=list)
    template_type: Literal["standard", "new_standard", "simple"] = "standard"


class ExamPaperCreateRequest(BaseModel):
    grade: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    question_types: list[str] = Field(min_length=1)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    count: int = Field(default=10, ge=1, le=50)


class AnalysisCreateRequest(BaseModel):
    class_name: str = Field(min_length=1)
    analysis_type: Literal["mistake", "score", "overview"] = "overview"
    data_source: dict[str, Any] = Field(default_factory=dict)


# ---------- 学习计划 ----------
class PlanCreateRequest(BaseModel):
    goal_description: str = Field(min_length=1)
    current_level: str = Field(min_length=1)
    days: int = Field(ge=1, le=365)


class PlanCheckinRequest(BaseModel):
    day: int = Field(ge=1)
    note: Optional[str] = None


# ---------- 记忆卡片 ----------
class FlashcardCreateRequest(BaseModel):
    """手动创建记忆卡片。"""

    front: str = Field(min_length=1)
    back: str = Field(min_length=1)
    subject: Optional[str] = None
    deck: str = Field(default="default", min_length=1)


class FlashcardUpdateRequest(BaseModel):
    """更新记忆卡片（仅提交非空字段）。"""

    front: Optional[str] = Field(default=None, min_length=1)
    back: Optional[str] = Field(default=None, min_length=1)
    subject: Optional[str] = None
    deck: Optional[str] = Field(default=None, min_length=1)


class FlashcardGenerateRequest(BaseModel):
    content: str = Field(min_length=1, description="用于生成卡片的原始内容")
    count: int = Field(default=10, ge=1, le=50)
    subject: Optional[str] = None


class FlashcardReviewRequest(BaseModel):
    rating: Literal["again", "fuzzy", "good"]


# ---------- 口语对练 ----------
class VoiceEvaluateRequest(BaseModel):
    """口语评分：scenario 为场景 id，transcript 为口语转写文本（ASR 接入前可为空）。"""

    scenario: str = Field(min_length=1, max_length=50)
    transcript: str = Field(default="", max_length=10000)


# ---------- 通用 ----------
class TaskAcceptedResponse(BaseModel):
    """长任务统一响应：任务 ID + 轮询地址。"""

    id: str
    task_id: str
    status: str = "generating"
    poll_url: str
