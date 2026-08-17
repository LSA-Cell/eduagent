"""dream_runs ORM 模型（§4.2，DDL 见 init.sql 第 12 节）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from eduagent_shared.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DreamRun(Base):
    """夜间记忆整合运行记录（每用户每日最多一条 completed；RLS：仅本人行可见）。"""

    __tablename__ = "dream_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    phase: Mapped[str] = mapped_column(String(20), default="orient")  # orient/gather/consolidate/prune/completed/failed
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)  # 各阶段产物（只含计数/标签，无原文）
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 摘要已推送（NULL=待次日首启）
