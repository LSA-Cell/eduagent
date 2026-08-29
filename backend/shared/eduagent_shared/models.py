"""EduAgent ORM 模型。

与 infra/postgres/init.sql 的 DDL 对应（DDL 归 infra 维护，此处为应用侧映射）。
约定：
- 主键统一 UUID（Python 侧 uuid4 生成）；
- created_at/updated_at 使用 timezone-aware UTC 时间，Python 侧默认填充；
- 业务扩展数据一律 JSONB；PostgreSQL 保留字 metadata 通过 ``metadata_`` 属性 +
  ``Column("metadata")`` 映射规避冲突；
- 向量列使用 pgvector.sqlalchemy.Vector(1536)。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    """created_at / updated_at 公共列。"""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class User(Base, TimestampMixin):
    """用户表。is_paid 用于限流分层（免费/付费/教师）。"""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # phone/password_hash 可空：游客（设备 ID）与第三方登录账号没有手机号/密码
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255))  # bcrypt 哈希，永不回显
    grade_level: Mapped[str | None] = mapped_column(String(50))  # 如 "初三" / "高二" / "大一"
    default_subject: Mapped[str | None] = mapped_column(String(50))
    is_teacher: Mapped[bool] = mapped_column(Boolean, default=False)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    # 游客账号：由设备 ID 签发，可通过 /auth/upgrade 绑定手机号转正
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False)
    device_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # 第三方登录标识（同一 provider 唯一）
    wechat_openid: Mapped[str | None] = mapped_column(String(64), unique=True)
    apple_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    # 身份模式：student / teacher / parent（PUT /users/me/mode 切换）
    user_mode: Mapped[str] = mapped_column(String(20), default="student")
    teacher_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserApiKey(Base, TimestampMixin):
    """用户自备第三方大模型 API Key（AES-256-GCM 密文存储，任何接口不回显明文）。"""

    __tablename__ = "user_api_keys"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_user_api_keys_user_provider"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(50))  # openai / deepseek / claude / gemini
    encrypted_key: Mapped[str] = mapped_column(Text)  # base64(nonce || 密文 || tag)
    # 额度单位：USD 美分；monthly_budget 为 NULL 表示不限额
    monthly_budget: Mapped[int | None] = mapped_column(BigInteger)
    used_budget: Mapped[int] = mapped_column(BigInteger, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ChatSession(Base, TimestampMixin):
    """聊天会话。"""

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(200))
    subject: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(100))
    inference_source: Mapped[str | None] = mapped_column(String(20))  # internal / external
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Message(Base, TimestampMixin):
    """会话消息。parent_id 支持分支对话树。"""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    role: Mapped[str] = mapped_column(String(20))  # system / user / assistant / tool
    content_type: Mapped[str] = mapped_column(String(20), default="text")  # text / image / audio / file
    content: Mapped[str] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class MistakeRecord(Base, TimestampMixin):
    """错题本。"""

    __tablename__ = "mistake_records"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    subject: Mapped[str | None] = mapped_column(String(50))
    question: Mapped[str] = mapped_column(Text)
    wrong_answer: Mapped[str | None] = mapped_column(Text)
    correct_answer: Mapped[str | None] = mapped_column(Text)
    analysis: Mapped[str | None] = mapped_column(Text)  # 错因分析
    knowledge_points: Mapped[list] = mapped_column(JSONB, default=list)
    mastery: Mapped[float] = mapped_column(Float, default=0.0)  # 掌握度 0~1
    source: Mapped[str | None] = mapped_column(String(50))  # chat / upload / exam
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Flashcard(Base, TimestampMixin):
    """记忆卡片（间隔重复）。"""

    __tablename__ = "flashcards"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    subject: Mapped[str | None] = mapped_column(String(50))
    deck: Mapped[str] = mapped_column(String(100), default="default")
    front: Mapped[str] = mapped_column(Text)
    back: Mapped[str] = mapped_column(Text)
    ease_factor: Mapped[float] = mapped_column(Float, default=2.5)
    interval_days: Mapped[int] = mapped_column(Integer, default=0)
    repetitions: Mapped[int] = mapped_column(Integer, default=0)
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class StudyPlan(Base, TimestampMixin):
    """学习计划。plan_content 为按天拆分的任务列表。"""

    __tablename__ = "study_plans"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str | None] = mapped_column(String(50))
    goal: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active / completed / archived
    plan_content: Mapped[dict] = mapped_column(JSONB, default=dict)
    progress: Mapped[float] = mapped_column(Float, default=0.0)  # 0~1
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class KnowledgeDocument(Base, TimestampMixin):
    """知识库文档（原始文件存 MinIO，object_key 为对象键）。"""

    __tablename__ = "knowledge_documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_type: Mapped[str | None] = mapped_column(String(50))
    object_key: Mapped[str | None] = mapped_column(String(500))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    subject: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # uploading / processing / completed / failed
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class KbChunk(Base, TimestampMixin):
    """知识库切片（RAG 向量检索单元）。"""

    __tablename__ = "kb_chunks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class Memory(Base, TimestampMixin):
    """长期记忆（画像 / 偏好 / 事件 / 摘要）。"""

    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    memory_type: Mapped[str] = mapped_column(String(30))  # profile / preference / event / summary
    content: Mapped[str] = mapped_column(Text)
    importance: Mapped[float] = mapped_column(Float, default=0.5)  # 0~1
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class LessonPlan(Base, TimestampMixin):
    """教案（教师端）。"""

    __tablename__ = "lesson_plans"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str | None] = mapped_column(String(50))
    grade_level: Mapped[str | None] = mapped_column(String(50))
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    objectives: Mapped[list] = mapped_column(JSONB, default=list)  # 教学目标
    content: Mapped[dict] = mapped_column(JSONB, default=dict)  # 教学环节结构化内容
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft / published
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Presentation(Base, TimestampMixin):
    """PPT 课件（生成文件存 MinIO generated 桶）。"""

    __tablename__ = "presentations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str | None] = mapped_column(String(50))
    grade_level: Mapped[str | None] = mapped_column(String(50))
    theme: Mapped[str | None] = mapped_column(String(100))
    slide_count: Mapped[int] = mapped_column(Integer, default=0)
    outline: Mapped[list] = mapped_column(JSONB, default=list)
    object_key: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft / generating / ready / failed
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ExamPaper(Base, TimestampMixin):
    """试卷（教师端）。questions 为结构化题目数组，answer_key 可选。"""

    __tablename__ = "exam_papers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str | None] = mapped_column(String(50))
    grade_level: Mapped[str | None] = mapped_column(String(50))
    total_score: Mapped[int | None] = mapped_column(Integer)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    questions: Mapped[list] = mapped_column(JSONB, default=list)
    answer_key: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class LearningAnalysis(Base, TimestampMixin):
    """学情分析报告（周期性的学习数据洞察）。"""

    __tablename__ = "learning_analyses"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )  # 被分析的学生
    analysis_type: Mapped[str] = mapped_column(String(30))  # weekly / monthly / custom
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    summary: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)  # 指标：正确率/时长/薄弱点等
    suggestions: Mapped[list] = mapped_column(JSONB, default=list)
