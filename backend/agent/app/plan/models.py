"""plan_documents ORM 模型（对应扩展包 §七 DDL，建表归 infra/postgres/init.sql）。

confirm_token 只存 SHA-256 哈希（64 hex），绑定 plan_id + user_id（同行校验），
30 分钟过期；修改产生的新版本以 parent_plan_id 指回旧版本，旧版本保留不覆盖。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from eduagent_shared.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PlanDocument(Base):
    """Plan Mode 大纲文档（每次修改产生一行新版本）。"""

    __tablename__ = "plan_documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    skill_code: Mapped[str] = mapped_column(String(30))  # ppt/lesson_plan/exam_paper/study_plan/analysis
    version: Mapped[int] = mapped_column(Integer, default=1)
    outline: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="drafting")
    # drafting/awaiting_confirm/confirmed/generating/completed/cancelled/expired
    confirm_token_hash: Mapped[str | None] = mapped_column(String(64))  # 仅存 SHA-256 哈希
    confirm_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parent_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("plan_documents.id"), nullable=True
    )  # 修改产生的新版本指回旧版本
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
