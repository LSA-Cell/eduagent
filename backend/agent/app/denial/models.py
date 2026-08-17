"""denial_counters ORM 模型（§3.3，DDL 见 infra/postgres/init.sql 第 11 节）。

(user_id, target_code) 维度记录 consecutive/total 拒绝计数；
target 可为 Skill、工具、追问推荐类别。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from eduagent_shared.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DenialCounter(Base):
    """拒绝计数（复合主键 user_id+target_code；RLS：仅本人可见可写）。"""

    __tablename__ = "denial_counters"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    target_code: Mapped[str] = mapped_column(String(50), primary_key=True)
    consecutive: Mapped[int] = mapped_column(Integer, default=0)  # 连续拒绝次数
    total: Mapped[int] = mapped_column(Integer, default=0)  # 累计拒绝次数
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
