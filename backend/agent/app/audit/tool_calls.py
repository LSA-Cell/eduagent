"""tool_call_audits 薄写入（fail-soft：失败仅记日志）。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.audit.models import ToolCallAudit
from app.audit.runs import _as_uuid

logger = logging.getLogger(__name__)


async def write_tool_call_audit(
    session: Any,
    *,
    run_id: Any = None,
    tool_name: str,
    input: Optional[dict] = None,
    decision: str = "allow",
    latency_ms: int = 0,
    result_size: int = 0,
    card_type: Optional[str] = None,
) -> None:
    """写入一条工具调用审计；失败仅记日志。"""
    try:
        session.add(
            ToolCallAudit(
                run_id=_as_uuid(run_id),
                tool_name=str(tool_name)[:50],
                input=input or {},
                decision=decision,
                latency_ms=int(latency_ms),
                result_size=int(result_size),
                card_type=card_type,
            )
        )
        await session.flush()
    except Exception:  # noqa: BLE001 - 审计失败不影响主流程
        logger.warning("write_tool_call_audit 落库失败", exc_info=True)
