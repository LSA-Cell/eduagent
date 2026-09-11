"""订单路由：POST /billing/orders。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from ..deps import get_billing_store, require_user_id
from ..errors import BillingError
from ..schemas import OrderCreate, OrderOut
from ..services.payments import get_provider
from ..services.store import BillingStore

router = APIRouter(prefix="/billing", tags=["billing"])


@router.post("/orders", response_model=OrderOut, status_code=201)
async def create_order(
    payload: OrderCreate,
    user_id: uuid.UUID = Depends(require_user_id),
    store: BillingStore = Depends(get_billing_store),
) -> OrderOut:
    """下单：校验套餐与渠道 → 创建 pending 订单 → 返回渠道支付参数。

    支付完成由渠道 webhook 驱动（payment_succeeded → 激活订阅），
    本接口不做任何激活动作（防客户端伪造支付成功）。
    """
    plan = await store.get_plan(payload.plan_id)
    if plan is None or not plan.is_active:
        raise BillingError(404, "套餐不存在或已下架", "invalid_request_error", "plan_not_found")
    if plan.price_cents <= 0:
        raise BillingError(
            400, "免费套餐无需购买", "invalid_request_error", "free_plan_not_purchasable"
        )
    provider = get_provider(payload.channel)
    if provider is None:
        raise BillingError(
            400,
            f"支付渠道 {payload.channel} 暂未接入",
            "invalid_request_error",
            "channel_not_supported",
        )
    order = await store.create_order(
        user_id=user_id,
        plan_id=plan.id,
        amount_cents=plan.price_cents,
        currency=plan.currency,
        channel=payload.channel,
        status="pending",
    )
    return OrderOut(
        id=order.id,
        plan_id=order.plan_id,
        amount_cents=order.amount_cents,
        currency=order.currency,
        channel=order.channel,
        status=order.status,
        created_at=order.created_at,
        paid_at=order.paid_at,
        payment_params=provider.create_payment_params(order.id, plan.id),
    )
