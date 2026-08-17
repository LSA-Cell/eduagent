"""POST /v1/guardrails/confirms：护栏 ask 确认回传端点。

闭环取舍（与 app/guardrails/pending.py docstring 一致，幂等重发方案）：
  1. Loop 遇 ask → 推 guardrail.ask（含 confirm_token），该调用按"待确认"回注；
  2. 客户端调本端点确认/拒绝（120s 未确认自动过期，按 deny 处理）；
  3. approved=true 后，客户端**重发原聊天请求**并携带 confirm_token；
     执行器对同一用户的同名工具调用一次性放行（consume 即失效，防重放）。
牺牲一次请求往返，换来无跨请求协程状态、断线可恢复、天然幂等。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app import deps as app_deps
from app.schemas import GuardrailConfirmRequest

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1",
    tags=["guardrails"],
    dependencies=[Depends(app_deps.require_internal_auth)],
)


@router.post("/guardrails/confirms")
async def confirm_guardrail(
    req: GuardrailConfirmRequest,
    user_id: str = Depends(app_deps.get_user_id),
    orch_deps: Any = Depends(app_deps.get_orchestration_deps),
) -> dict[str, Any]:
    """确认/拒绝一次挂起的工具调用。"""
    store = getattr(orch_deps, "pending_confirms", None)
    if store is None:
        raise HTTPException(status_code=503, detail="确认通道未启用")
    item = store.decide(req.confirm_token, user_id=user_id, approved=req.approved)
    if item is None:
        # 不存在 / 非本人 / 已过期（120s 超时按 deny 语义）
        raise HTTPException(
            status_code=404, detail="confirm token 不存在或已过期（超时按拒绝处理）"
        )

    # 决策审计（fail-soft）：复用 guardrail_decisions 通道
    audit = getattr(orch_deps, "audit", None)
    if audit is not None:
        try:
            await audit.write_decision(
                user_id=user_id,
                run_id=item.run_id,
                tool_name=item.tool_name,
                stage="parental",
                decision="allow" if req.approved else "deny",
                reason="用户经 confirm_token 确认" if req.approved else "用户经 confirm_token 拒绝",
            )
        except Exception:  # noqa: BLE001
            logger.warning("确认决策审计写入失败", exc_info=True)

    if req.approved:
        return {
            "status": "approved",
            "tool_name": item.tool_name,
            "next": "请重发原聊天请求并携带 confirm_token，挂起的工具调用将被一次性放行",
        }
    return {"status": "denied", "tool_name": item.tool_name}
