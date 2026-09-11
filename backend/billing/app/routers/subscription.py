"""订阅路由：GET /billing/subscription。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from ..deps import get_billing_store, require_user_id
from ..schemas import PlanOut, QuotaOut, SubscriptionOut
from ..services import ops
from ..services.store import BillingStore

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/subscription", response_model=SubscriptionOut)
async def get_subscription(
    user_id: uuid.UUID = Depends(require_user_id),
    store: BillingStore = Depends(get_billing_store),
) -> SubscriptionOut:
    """当前订阅 + 本期额度用量（惰性过期在此触发）。"""
    status = await ops.subscription_status(store, user_id)
    quota = status["quota"]
    return SubscriptionOut(
        plan=PlanOut.model_validate(status["plan"], from_attributes=True),
        status=status["status"],
        channel=status["channel"],
        current_period_start=status["current_period_start"],
        current_period_end=status["current_period_end"],
        auto_renew=status["auto_renew"],
        quota=QuotaOut(
            period_start=quota.period_start,
            quota_cents=quota.quota_cents,
            used_cents=quota.used_cents,
            remaining_cents=max(0, quota.quota_cents - quota.used_cents),
        ),
    )
