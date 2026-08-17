"""Plan Mode 确认协议端点（增强机制 §2.1）：confirm / revise / cancel。

- POST /v1/plans/{plan_id}/confirm：confirm_token 校验（哈希+归属+30min 有效）
  → confirmed → 建生成记录 → 投递 Celery（入参携带 outline_snapshot，
  前置状态校验强制，未 confirmed 禁止投递）；
- POST /v1/plans/{plan_id}/revise：修改产生新 plan_version（parent_plan_id
  指回旧版本，旧版本保留且令牌作废）；
- POST /v1/plans/{plan_id}/cancel：→ cancelled。

状态迁移的 plan.status SSE 事件在 Loop 运行内推送；REST 侧迁移无活动流，
直接以响应体返回最新状态（客户端据此刷新卡片）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import ValidationError

from app import deps as app_deps
from app.plan.dispatch import (
    PlanDispatchError,
    create_generation_record,
    dispatch_plan_generation,
)
from app.plan.manager import (
    PlanError,
    PlanManager,
    PlanNotFoundError,
    PlanPermissionError,
    PlanTokenError,
)
from app.plan.state_machine import PlanStateError
from app.schemas import PlanConfirmRequest, PlanReviseRequest

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/plans",
    tags=["orchestration-plan"],
    dependencies=[Depends(app_deps.require_internal_auth)],
)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PlanNotFoundError):
        return HTTPException(status_code=404, detail="plan 不存在")
    if isinstance(exc, PlanPermissionError):
        return HTTPException(status_code=403, detail="无权操作该 plan")
    if isinstance(exc, PlanTokenError):
        return HTTPException(status_code=410, detail=str(exc))
    if isinstance(exc, (PlanStateError, PlanDispatchError)):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _plan_view(plan: Any, *, confirm_token: Any = None) -> dict[str, Any]:
    view = {
        "plan_id": str(plan.id),
        "version": int(plan.version),
        "skill_code": plan.skill_code,
        "status": plan.status,
        "outline": plan.outline,
        "parent_plan_id": str(plan.parent_plan_id) if plan.parent_plan_id else None,
    }
    if confirm_token:
        view["confirm_token"] = confirm_token
    return view


@router.post("/{plan_id}/confirm")
async def confirm_plan(
    plan_id: str,
    req: PlanConfirmRequest,
    x_user_id: str = Header(default="", alias="X-User-Id"),
    orch_deps: Any = Depends(app_deps.get_orchestration_deps),
) -> dict[str, Any]:
    """确认大纲 → 投递生成任务（携带确认时的大纲快照）。"""
    manager = PlanManager()
    try:
        async with orch_deps.session_factory() as session:
            plan = await manager.confirm(
                session, plan_id, user_id=x_user_id, token=req.confirm_token
            )
            # 先建生成记录行（失败→422，plan 保持 confirmed 可重试投递）
            record_id = await create_generation_record(session, x_user_id, plan)
            await session.commit()
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"大纲参数不足: {exc.errors()[:3]}") from exc
    except (PlanError, PlanDispatchError) as exc:
        raise _http_error(exc) from exc

    try:
        dispatch_plan_generation(orch_deps, plan, record_id=record_id)
    except PlanDispatchError as exc:
        raise _http_error(exc) from exc
    # §5.4：plan.confirmed 自动化 Hook（fail-soft，异步派发，不影响确认结果）
    try:
        from app.hooks.dispatcher import dispatch_event

        await dispatch_event(
            "plan.confirmed",
            {
                "user_id": str(x_user_id),
                "plan_id": str(plan.id),
                "skill_code": plan.skill_code,
                "version": int(plan.version),
            },
            session_factory=orch_deps.session_factory,
        )
    except Exception:  # noqa: BLE001 - Hook 失败不影响主流程
        logger.warning("plan.confirmed hook 派发失败", exc_info=True)
    return {**_plan_view(plan), "record_id": record_id, "dispatched": True}


@router.post("/{plan_id}/revise")
async def revise_plan(
    plan_id: str,
    req: PlanReviseRequest,
    x_user_id: str = Header(default="", alias="X-User-Id"),
    orch_deps: Any = Depends(app_deps.get_orchestration_deps),
) -> dict[str, Any]:
    """修改大纲：产生新 plan_version（旧版本保留，令牌作废），返回新令牌。"""
    manager = PlanManager()
    try:
        async with orch_deps.session_factory() as session:
            new_plan, token = await manager.revise(
                session, plan_id, user_id=x_user_id, outline=req.outline
            )
            await session.commit()
    except PlanError as exc:
        raise _http_error(exc) from exc
    return _plan_view(new_plan, confirm_token=token)


@router.post("/{plan_id}/cancel")
async def cancel_plan(
    plan_id: str,
    x_user_id: str = Header(default="", alias="X-User-Id"),
    orch_deps: Any = Depends(app_deps.get_orchestration_deps),
) -> dict[str, Any]:
    """取消大纲。"""
    manager = PlanManager()
    try:
        async with orch_deps.session_factory() as session:
            plan = await manager.cancel(session, plan_id, user_id=x_user_id)
            await session.commit()
    except PlanError as exc:
        raise _http_error(exc) from exc
    return _plan_view(plan)
