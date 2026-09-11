"""套餐路由：GET /billing/plans。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import get_billing_store
from ..schemas import PlanOut
from ..services.store import BillingStore

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(store: BillingStore = Depends(get_billing_store)) -> list[PlanOut]:
    """在售套餐列表（公开展示数据，按 sort_order 升序）。"""
    plans = await store.list_active_plans()
    return [PlanOut.model_validate(p, from_attributes=True) for p in plans]
