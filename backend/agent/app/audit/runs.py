"""agent_runs / context_compactions 薄写入（fail-soft：失败仅记日志）。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.audit.models import AgentRun, ContextCompaction

logger = logging.getLogger(__name__)


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    """宽松 UUID 转换；非法值返回 None（审计不强求外键有效）。"""
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


async def write_agent_run(
    session: Any,
    *,
    run_id: Any,
    session_id: Any = None,
    user_id: Any = None,
    status: str = "running",
    turn_count: int = 0,
    model_route: Optional[str] = None,
    token_usage: Optional[dict] = None,
    finished_at: Any = None,
) -> None:
    """写入一条 Loop 运行记录；失败仅记日志。"""
    try:
        session.add(
            AgentRun(
                id=_as_uuid(run_id) or uuid.uuid4(),
                session_id=_as_uuid(session_id),
                user_id=_as_uuid(user_id),
                status=status,
                turn_count=turn_count,
                model_route=model_route,
                token_usage=token_usage or {},
                started_at=datetime.now(timezone.utc),
                finished_at=finished_at,
            )
        )
        await session.flush()
    except Exception:  # noqa: BLE001 - 审计失败不影响主流程
        logger.warning("write_agent_run 落库失败", exc_info=True)


async def write_compaction_audit(
    session: Any,
    *,
    session_id: Any = None,
    level: str,
    tokens_before: int,
    tokens_after: int,
    summary: str = "",
    boundary_message_id: Any = None,
) -> None:
    """写入一条压缩审计；失败仅记日志。"""
    try:
        session.add(
            ContextCompaction(
                session_id=_as_uuid(session_id),
                level=level,
                tokens_before=int(tokens_before),
                tokens_after=int(tokens_after),
                summary=str(summary)[:2000],
                boundary_message_id=_as_uuid(boundary_message_id),
            )
        )
        await session.flush()
    except Exception:  # noqa: BLE001
        logger.warning("write_compaction_audit 落库失败", exc_info=True)
