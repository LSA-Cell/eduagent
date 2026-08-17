"""stop_hook_audits ORM 模型（对应扩展包 §七 DDL，建表归 infra/postgres/init.sql）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from eduagent_shared.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StopHookAudit(Base):
    """Stop Hook 校验记录（每次校验一行，含强制继续序号与失败原因）。"""

    __tablename__ = "stop_hook_audits"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True, index=True
    )
    hook_name: Mapped[str] = mapped_column(String(50))  # math_verify/grading_consistency/completeness_check
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)  # 失败原因、复核数据
    forced_continue_count: Mapped[int] = mapped_column(Integer, default=0)  # 第几次强制继续
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
