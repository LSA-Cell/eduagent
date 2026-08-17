"""编排层审计表 ORM 模型（对应设计文档第六章 DDL，建表归 infra/Wave B）。

字段与 DDL 对齐；含 user_id 的表在 DDL 层启用 RLS（策略 user_id =
current_setting('app.user_id')），应用侧只负责写入。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from eduagent_shared.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentRun(Base):
    """Agent Loop 运行记录（每次 Loop 一条）。"""

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    model_route: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running/completed/failed/circuit_broken
    token_usage: Mapped[dict] = mapped_column(JSONB, default=dict)  # {input, output, by_turn: []}
    todo_snapshot: Mapped[dict] = mapped_column(JSONB, default=list)  # Wave C：AgentTodo 快照（回放/审计）
    timings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # Wave D2：分段 Checkpoint（§5.1，只含毫秒计时）
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToolCallAudit(Base):
    """工具调用审计（含护栏决策结果与执行指标）。"""

    __tablename__ = "tool_call_audits"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(50))
    input: Mapped[dict] = mapped_column(JSONB, default=dict)
    decision: Mapped[str] = mapped_column(String(10), default="allow")  # allow/ask/deny
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    result_size: Mapped[int] = mapped_column(Integer, default=0)
    card_type: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class GuardrailDecision(Base):
    """护栏决策审计（每次工具调用的每一环节一行）。"""

    __tablename__ = "guardrail_decisions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(50))
    stage: Mapped[str] = mapped_column(String(20))  # moderation/role/quota/parental
    decision: Mapped[str] = mapped_column(String(10))  # allow/ask/deny
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ContextCompaction(Base):
    """上下文压缩审计（四级管线每触发一次一行）。"""

    __tablename__ = "context_compactions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id"), nullable=True
    )
    level: Mapped[str] = mapped_column(String(20))  # slim/microcompact/autocompact/reactive
    tokens_before: Mapped[int] = mapped_column(Integer, default=0)
    tokens_after: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")
    boundary_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
