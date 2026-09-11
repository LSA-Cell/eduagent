"""订阅生命周期操作：激活 / 惰性过期 / 状态查询（路由与 webhook 共用）。"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from eduagent_shared.logging import get_logger

from .store import BillingStore

log = get_logger("billing.ops")

FREE_PLAN_ID = "free"


def current_period_start() -> date:
    """当月 1 号（额度周期边界）。"""
    today = datetime.now(timezone.utc).date()
    return today.replace(day=1)


async def expire_if_needed(store: BillingStore, sub: Any | None) -> Any | None:
    """惰性过期：active 订阅过了 current_period_end 即置 expired 并同步 is_paid=False。

    代替定时扫描任务（spec §1.2）；所有读路径先过此函数。
    """
    if sub is None or sub.status != "active":
        return sub
    now = datetime.now(timezone.utc)
    period_end = sub.current_period_end
    if period_end.tzinfo is None:  # InMemory/朴素时间防御
        period_end = period_end.replace(tzinfo=timezone.utc)
    if period_end <= now:
        await store.set_subscription_status(sub.id, "expired")
        await store.set_user_paid(sub.user_id, False)
        log.info("subscription_expired", sub_id=str(sub.id), user_id=str(sub.user_id))
        return None
    return sub


async def activate_subscription(
    store: BillingStore,
    *,
    user_id: uuid.UUID,
    plan: Any,
    channel: str,
    channel_sub_id: str | None,
    days: int = 30,
) -> Any:
    """激活订阅：旧 active 订阅作废 → 新订阅生效 → is_paid 同步 → 当月额度对齐。

    - 免费套餐不可激活（免费额度由 quota 惰性创建覆盖）；
    - 同一支付成功的重复调用由订单状态机守卫（幂等在路由/webhook 层）。
    """
    now = datetime.now(timezone.utc)
    old = await expire_if_needed(store, await store.get_active_subscription(user_id))
    if old is not None:
        # 换购/续期：旧订阅立即作废，新周期从 now 起算（v1 不做 proration，spec §1.2）
        await store.set_subscription_status(old.id, "expired")
    sub = await store.create_subscription(
        user_id=user_id,
        plan_id=plan.id,
        status="active",
        channel=channel,
        channel_sub_id=channel_sub_id,
        current_period_start=now,
        current_period_end=now + timedelta(days=days),
        auto_renew=True,
    )
    await store.set_user_paid(user_id, True)
    await store.ensure_quota_period(
        user_id, current_period_start(), plan.monthly_quota_cents
    )
    log.info(
        "subscription_activated",
        sub_id=str(sub.id),
        user_id=str(user_id),
        plan_id=plan.id,
        channel=channel,
    )
    return sub


async def subscription_status(store: BillingStore, user_id: uuid.UUID) -> dict:
    """组装订阅状态响应：当前订阅（惰性过期后）+ 本期额度（按 free 惰性创建）。"""
    sub = await expire_if_needed(store, await store.get_active_subscription(user_id))
    if sub is not None:
        plan = await store.get_plan(sub.plan_id)
    else:
        plan = await store.get_plan(FREE_PLAN_ID)
    if plan is None:  # 种子数据缺失的防御：零额度，平台兜底一律 402
        plan = type(
            "EmptyPlan",
            (),
            {
                "id": FREE_PLAN_ID,
                "name": "免费版",
                "price_cents": 0,
                "currency": "CNY",
                "monthly_quota_cents": 0,
                "allowed_models": [],
                "features": {},
                "sort_order": 0,
            },
        )()
    quota = await store.ensure_quota_period(
        user_id, current_period_start(), plan.monthly_quota_cents
    )
    return {
        "plan": plan,
        "status": "active" if sub is not None else "free",
        "channel": sub.channel if sub is not None else None,
        "current_period_start": sub.current_period_start if sub is not None else None,
        "current_period_end": sub.current_period_end if sub is not None else None,
        "auto_renew": sub.auto_renew if sub is not None else True,
        "quota": quota,
    }
