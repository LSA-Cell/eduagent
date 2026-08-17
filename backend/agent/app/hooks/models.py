"""automation_hooks ORM 模型（§5.4，DDL 见 init.sql 第 12 节）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from eduagent_shared.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AutomationHook(Base):
    """event → action 配置（RLS：按配置者 owner_id 隔离）。"""

    __tablename__ = "automation_hooks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    event: Mapped[str] = mapped_column(String(40))  # mistake.created/plan.confirmed/dream.completed
    action: Mapped[str] = mapped_column(String(40))  # notify_parent/notify_teacher
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
