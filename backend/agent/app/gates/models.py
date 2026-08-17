"""feature_gates ORM 模型（§5.2，DDL 见 init.sql 第 12 节；全局只读表，无 RLS）。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy import Uuid
from sqlalchemy.orm import Mapped, mapped_column

from eduagent_shared.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FeatureGate(Base):
    """灰度配置行：key / enabled / rollout_pct / allowlist（应用角色只读）。"""

    __tablename__ = "feature_gates"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # 新机制默认 off
    rollout_pct: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    allowlist: Mapped[list | None] = mapped_column(ARRAY(Uuid), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
