"""仓储层：套餐 / 订阅 / 订单 / 额度周期 的读写。

- 协议（Protocol）定义接口，便于测试用内存实现替换（dependency override），
  避免测试依赖真实 PostgreSQL（模型含 UUID/JSONB 等 PG 专属类型，无法跑 SQLite）。
- SqlAlchemyBillingStore 为生产实现；InMemoryBillingStore 为测试实现，
  属性名与 ORM 模型保持一致。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from eduagent_shared.models import Order, Plan, QuotaPeriod, Subscription, User


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BillingStore(Protocol):
    async def list_active_plans(self) -> list[Any]: ...
    async def get_plan(self, plan_id: str) -> Any | None: ...
    async def get_active_subscription(self, user_id: uuid.UUID) -> Any | None: ...
    async def create_subscription(self, **kwargs) -> Any: ...
    async def set_subscription_status(self, sub_id: uuid.UUID, status: str) -> None: ...
    async def create_order(self, **kwargs) -> Any: ...
    async def get_order(self, order_id: uuid.UUID) -> Any | None: ...
    async def get_order_by_txn(self, channel_txn_id: str) -> Any | None: ...
    async def mark_order_paid(
        self, order_id: uuid.UUID, channel_txn_id: str, paid_at: datetime
    ) -> Any | None: ...
    async def set_user_paid(self, user_id: uuid.UUID, is_paid: bool) -> None: ...
    async def get_quota_period(
        self, user_id: uuid.UUID, period_start: date
    ) -> Any | None: ...
    async def ensure_quota_period(
        self, user_id: uuid.UUID, period_start: date, quota_cents: int
    ) -> Any: ...


class SqlAlchemyBillingStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active_plans(self) -> list[Plan]:
        result = await self.session.execute(
            select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order)
        )
        return list(result.scalars())

    async def get_plan(self, plan_id: str) -> Plan | None:
        return await self.session.get(Plan, plan_id)

    async def get_active_subscription(self, user_id: uuid.UUID) -> Subscription | None:
        result = await self.session.execute(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == "active",
            )
            .order_by(Subscription.current_period_end.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create_subscription(self, **kwargs) -> Subscription:
        sub = Subscription(**kwargs)
        self.session.add(sub)
        await self.session.commit()
        await self.session.refresh(sub)
        return sub

    async def set_subscription_status(self, sub_id: uuid.UUID, status: str) -> None:
        await self.session.execute(
            update(Subscription)
            .where(Subscription.id == sub_id)
            .values(status=status, updated_at=_utcnow())
        )
        await self.session.commit()

    async def create_order(self, **kwargs) -> Order:
        order = Order(**kwargs)
        self.session.add(order)
        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def get_order(self, order_id: uuid.UUID) -> Order | None:
        return await self.session.get(Order, order_id)

    async def get_order_by_txn(self, channel_txn_id: str) -> Order | None:
        result = await self.session.execute(
            select(Order).where(Order.channel_txn_id == channel_txn_id)
        )
        return result.scalar_one_or_none()

    async def mark_order_paid(
        self, order_id: uuid.UUID, channel_txn_id: str, paid_at: datetime
    ) -> Order | None:
        """pending → paid 状态机守卫（并发/重放安全）；已 paid 返回 None。"""
        result = await self.session.execute(
            update(Order)
            .where(Order.id == order_id, Order.status == "pending")
            .values(status="paid", channel_txn_id=channel_txn_id, paid_at=paid_at)
            .returning(Order.id)
        )
        if result.scalar_one_or_none() is None:
            return None
        await self.session.commit()
        return await self.session.get(Order, order_id)

    async def set_user_paid(self, user_id: uuid.UUID, is_paid: bool) -> None:
        """同步 users.is_paid：限流分层（RateTierResolver）零改动生效。"""
        await self.session.execute(
            update(User).where(User.id == user_id).values(is_paid=is_paid)
        )
        await self.session.commit()

    async def get_quota_period(
        self, user_id: uuid.UUID, period_start: date
    ) -> QuotaPeriod | None:
        return await self.session.get(QuotaPeriod, (user_id, period_start))

    async def ensure_quota_period(
        self, user_id: uuid.UUID, period_start: date, quota_cents: int
    ) -> QuotaPeriod:
        """不存在则创建；已存在则额度只升不降（升级套餐当月即时生效）。"""
        row = await self.get_quota_period(user_id, period_start)
        if row is None:
            row = QuotaPeriod(
                user_id=user_id, period_start=period_start, quota_cents=quota_cents
            )
            self.session.add(row)
            await self.session.commit()
            await self.session.refresh(row)
            return row
        if quota_cents > row.quota_cents:
            await self.session.execute(
                update(QuotaPeriod)
                .where(
                    QuotaPeriod.user_id == user_id,
                    QuotaPeriod.period_start == period_start,
                )
                .values(quota_cents=quota_cents, updated_at=_utcnow())
            )
            await self.session.commit()
            await self.session.refresh(row)
        return row


# ----------------------------------------------------------------------------
# 测试用内存实现
# ----------------------------------------------------------------------------


@dataclass
class PlanRecord:
    id: str
    name: str
    price_cents: int
    monthly_quota_cents: int
    currency: str = "CNY"
    allowed_models: list = field(default_factory=list)
    features: dict = field(default_factory=dict)
    sort_order: int = 0
    is_active: bool = True
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class SubRecord:
    user_id: uuid.UUID
    plan_id: str
    status: str
    channel: str
    current_period_start: datetime
    current_period_end: datetime
    channel_sub_id: str | None = None
    auto_renew: bool = True
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


@dataclass
class OrderRecord:
    user_id: uuid.UUID
    plan_id: str
    amount_cents: int
    channel: str
    status: str = "pending"
    currency: str = "CNY"
    channel_txn_id: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=_utcnow)
    paid_at: datetime | None = None


@dataclass
class QuotaRecord:
    user_id: uuid.UUID
    period_start: date
    quota_cents: int
    used_cents: int = 0
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


class InMemoryBillingStore:
    """测试用内存实现（含 users.is_paid 影子表）。"""

    def __init__(self) -> None:
        self.plans: dict[str, PlanRecord] = {}
        self.subs: dict[uuid.UUID, SubRecord] = {}
        self.orders: dict[uuid.UUID, OrderRecord] = {}
        self.quotas: dict[tuple[uuid.UUID, date], QuotaRecord] = {}
        self.user_paid: dict[uuid.UUID, bool] = {}

    def seed_plan(self, **kwargs) -> PlanRecord:
        plan = PlanRecord(**kwargs)
        self.plans[plan.id] = plan
        return plan

    async def list_active_plans(self) -> list[PlanRecord]:
        return sorted(
            (p for p in self.plans.values() if p.is_active), key=lambda p: p.sort_order
        )

    async def get_plan(self, plan_id: str) -> PlanRecord | None:
        return self.plans.get(plan_id)

    async def get_active_subscription(self, user_id: uuid.UUID) -> SubRecord | None:
        actives = [
            s for s in self.subs.values() if s.user_id == user_id and s.status == "active"
        ]
        if not actives:
            return None
        return max(actives, key=lambda s: s.current_period_end)

    async def create_subscription(self, **kwargs) -> SubRecord:
        sub = SubRecord(**kwargs)
        self.subs[sub.id] = sub
        return sub

    async def set_subscription_status(self, sub_id: uuid.UUID, status: str) -> None:
        self.subs[sub_id].status = status
        self.subs[sub_id].updated_at = _utcnow()

    async def create_order(self, **kwargs) -> OrderRecord:
        order = OrderRecord(**kwargs)
        self.orders[order.id] = order
        return order

    async def get_order(self, order_id: uuid.UUID) -> OrderRecord | None:
        return self.orders.get(order_id)

    async def get_order_by_txn(self, channel_txn_id: str) -> OrderRecord | None:
        return next(
            (o for o in self.orders.values() if o.channel_txn_id == channel_txn_id), None
        )

    async def mark_order_paid(
        self, order_id: uuid.UUID, channel_txn_id: str, paid_at: datetime
    ) -> OrderRecord | None:
        order = self.orders.get(order_id)
        if order is None or order.status != "pending":
            return None
        order.status = "paid"
        order.channel_txn_id = channel_txn_id
        order.paid_at = paid_at
        return order

    async def set_user_paid(self, user_id: uuid.UUID, is_paid: bool) -> None:
        self.user_paid[user_id] = is_paid

    async def get_quota_period(
        self, user_id: uuid.UUID, period_start: date
    ) -> QuotaRecord | None:
        return self.quotas.get((user_id, period_start))

    async def ensure_quota_period(
        self, user_id: uuid.UUID, period_start: date, quota_cents: int
    ) -> QuotaRecord:
        key = (user_id, period_start)
        row = self.quotas.get(key)
        if row is None:
            row = QuotaRecord(
                user_id=user_id, period_start=period_start, quota_cents=quota_cents
            )
            self.quotas[key] = row
            return row
        if quota_cents > row.quota_cents:
            row.quota_cents = quota_cents
            row.updated_at = _utcnow()
        return row
