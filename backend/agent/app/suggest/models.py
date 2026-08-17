"""suggestion_logs ORM 模型（§4.1，DDL 见 init.sql 第 11 节）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from eduagent_shared.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SuggestionLog(Base):
    """追问推荐日志（items + clicked_index；RLS：仅本人行可见）。"""

    __tablename__ = "suggestion_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    items: Mapped[dict] = mapped_column(JSONB, default=list)  # 3 条候选（已过审）
    clicked_index: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 未点=NULL
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
