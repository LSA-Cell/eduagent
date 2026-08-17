"""output_styles ORM 模型（§5.3，DDL 见 init.sql 第 12 节；全局只读表，无 RLS）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String, Text, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from eduagent_shared.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OutputStyle(Base):
    """年级+学科 → 语气提示词（运营后台增改，进程内缓存 60s 热生效）。"""

    __tablename__ = "output_styles"
    __table_args__ = (
        UniqueConstraint("grade_level", "subject", name="uq_output_styles_grade_subject"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    grade_level: Mapped[str] = mapped_column(String(20))  # '*' 通配全部年级
    subject: Mapped[str] = mapped_column(String(20))  # '*' 通配全部学科
    tone_prompt: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
