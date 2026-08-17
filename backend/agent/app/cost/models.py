"""cost_ledger / model_prices ORM 模型（§3.4，DDL 见 init.sql 第 11 节）。

- cost_ledger：逐 run 记录 token 与成本（单价快照随行落库，对账可溯源）；
- model_prices：单价配置表（provider+model 维度，effective_from 支持历史
  快照；全局只读，不启用 RLS）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from eduagent_shared.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CostLedger(Base):
    """逐 run 成本台账（RLS：仅本人行可见；前端不暴露明细）。"""

    __tablename__ = "cost_ledger"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    model_route: Mapped[str | None] = mapped_column(String(50))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit_tokens: Mapped[int] = mapped_column(Integer, default=0)
    unit_price_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # {input_per_1k, output_per_1k, currency}（美分/1k tokens）；无单价记录时
    # 为 NULL（只记 tokens 不计费，见 pricing.PricingReader 语义）
    cost_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quota_tier: Mapped[str | None] = mapped_column(String(20))  # 身份档快照
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ModelPrice(Base):
    """模型单价配置（provider+model+effective_from 唯一；取最新生效行）。"""

    __tablename__ = "model_prices"
    __table_args__ = (
        UniqueConstraint("provider", "model", "effective_from", name="uq_model_prices_route_effective"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(50))
    input_per_1k_cents: Mapped[int] = mapped_column(Integer)  # 输入单价（美分/1k tokens）
    output_per_1k_cents: Mapped[int] = mapped_column(Integer)  # 输出单价（美分/1k tokens）
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
