"""guardrail_decisions 薄写入（fail-soft：失败仅记日志）。"""

from __future__ import annotations

import logging
from typing import Any

from app.audit.models import GuardrailDecision
from app.audit.runs import _as_uuid

logger = logging.getLogger(__name__)


async def write_guardrail_decision(
    session: Any,
    *,
    user_id: Any = None,
    run_id: Any = None,
    tool_name: str,
    stage: str,
    decision: str,
    reason: str = "",
) -> None:
    """写入一条护栏决策审计；失败仅记日志。"""
    try:
        session.add(
            GuardrailDecision(
                user_id=_as_uuid(user_id),
                run_id=_as_uuid(run_id),
                tool_name=str(tool_name)[:50],
                stage=stage,
                decision=decision,
                reason=str(reason)[:1000],
            )
        )
        await session.flush()
    except Exception:  # noqa: BLE001 - 审计失败不影响主流程
        logger.warning("write_guardrail_decision 落库失败", exc_info=True)
