"""admin 路由：POST /billing/admin/grant（手工开通/续期）。

仅 internal token 可调（路由级依赖）；用于测试全链路与客服补偿，
不产生真实订单支付流，订单以 channel=admin 直接 paid 落库备查。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from ..deps import get_billing_store, require_internal_token
from ..errors import BillingError
from ..schemas import AdminGrantRequest
from ..services import ops
from ..services.store import BillingStore

router = APIRouter(
    prefix="/billing/admin",
    tags=["billing-admin"],
    dependencies=[Depends(require_internal_token)],
)


@router.post("/grant", status_code=201)
async def grant_subscription(
    payload: AdminGrantRequest,
    store: BillingStore = Depends(get_billing_store),
) -> dict:
    plan = await store.get_plan(payload.plan_id)
    if plan is None or not plan.is_active:
        raise BillingError(404, "套餐不存在或已下架", "invalid_request_error", "plan_not_found")
    order = await store.create_order(
        user_id=payload.user_id,
        plan_id=plan.id,
        amount_cents=0,
        currency=plan.currency,
        channel="admin",
        status="pending",
    )
    await store.mark_order_paid(
        order.id, f"admin-grant:{order.id}", datetime.now(timezone.utc)
    )
    sub = await ops.activate_subscription(
        store,
        user_id=payload.user_id,
        plan=plan,
        channel="admin",
        channel_sub_id=None,
        days=payload.days,
    )
    return {
        "granted": True,
        "user_id": str(payload.user_id),
        "plan_id": plan.id,
        "subscription_id": str(sub.id),
        "current_period_end": sub.current_period_end.isoformat(),
    }
