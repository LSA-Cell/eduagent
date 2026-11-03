"""平台兜底额度闸门（Billing v1，指导/后端设计/计费/spec.md §4）。

规则：
- 有效套餐 = active 且未过期的订阅对应 plan；无订阅/已过期 = free 档；
- 平台兜底型号必须 ∈ plan.allowed_models（空前缀列表 = 全开放，前缀匹配与 pricing 一致）；
- 软闸门：used_cents < quota_cents 放行，调用后按实际成本原子累加（允许单次轻微超支）；
- 额度行不存在时按有效套餐惰性创建（新用户零初始化）。

订阅生命周期（激活/过期/is_paid 同步）归 billing 服务管；
proxy 只读订阅与额度行、累加 used_cents，不写订阅状态。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from eduagent_shared.models import Plan, QuotaPeriod, Subscription

from ..errors import ProxyError

FREE_PLAN_ID = "free"


def current_period_start() -> date:
    today = datetime.now(timezone.utc).date()
    return today.replace(day=1)


@dataclass
class QuotaContext:
    """闸门通过后的记账上下文（随请求传递到响应后的计费环节）。"""

    user_id: uuid.UUID
    period_start: date
    plan_id: str


class QuotaStore(Protocol):
    async def get_effective_plan(self, user_id: uuid.UUID) -> Any | None: ...
    async def get_quota_period(
        self, user_id: uuid.UUID, period_start: date
    ) -> Any | None: ...
    async def ensure_quota_period(
        self, user_id: uuid.UUID, period_start: date, quota_cents: int
    ) -> Any: ...
    async def add_used_cents(
        self, user_id: uuid.UUID, period_start: date, cents: int
    ) -> int: ...


class SqlAlchemyQuotaStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_effective_plan(self, user_id: uuid.UUID) -> Plan | None:
        """active 且未过期订阅的 plan；无则 free 档。free 种子缺失返回 None（调用方零额度处理）。"""
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(Plan)
            .join(Subscription, Subscription.plan_id == Plan.id)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == "active",
                Subscription.current_period_end > now,
            )
            .order_by(Subscription.current_period_end.desc())
            .limit(1)
        )
        plan = result.scalar_one_or_none()
        if plan is not None:
            return plan
        return await self.session.get(Plan, FREE_PLAN_ID)

    async def get_quota_period(
        self, user_id: uuid.UUID, period_start: date
    ) -> QuotaPeriod | None:
        return await self.session.get(QuotaPeriod, (user_id, period_start))

    async def ensure_quota_period(
        self, user_id: uuid.UUID, period_start: date, quota_cents: int
    ) -> QuotaPeriod:
        row = await self.session.get(QuotaPeriod, (user_id, period_start))
        if row is None:
            row = QuotaPeriod(
                user_id=user_id, period_start=period_start, quota_cents=quota_cents
            )
            self.session.add(row)
            await self.session.commit()
            await self.session.refresh(row)
            return row
        if quota_cents > row.quota_cents:
            # 套餐升级当月即时生效（与 billing 服务同一 upsert 规则）
            await self.session.execute(
                update(QuotaPeriod)
                .where(
                    QuotaPeriod.user_id == user_id,
                    QuotaPeriod.period_start == period_start,
                )
                .values(quota_cents=quota_cents, updated_at=datetime.now(timezone.utc))
            )
            await self.session.commit()
            await self.session.refresh(row)
        return row

    async def add_used_cents(
        self, user_id: uuid.UUID, period_start: date, cents: int
    ) -> int:
        """原子累加 used_cents（防并发覆盖），返回累加后的值。"""
        result = await self.session.execute(
            update(QuotaPeriod)
            .where(
                QuotaPeriod.user_id == user_id,
                QuotaPeriod.period_start == period_start,
            )
            .values(
                used_cents=QuotaPeriod.used_cents + cents,
                updated_at=datetime.now(timezone.utc),
            )
            .returning(QuotaPeriod.used_cents)
        )
        new_value = result.scalar_one()
        await self.session.commit()
        return int(new_value)


def model_allowed(plan: Any, model: str) -> bool:
    """空 allowed_models = 全开放；否则前缀匹配（与 pricing.price_for 同一约定）。"""
    allowed = getattr(plan, "allowed_models", None) or []
    if not allowed:
        return True
    return any(model.startswith(entry) for entry in allowed)


async def check_platform_quota(
    store: QuotaStore, user_id: uuid.UUID, model: str
) -> QuotaContext:
    """平台兜底闸门：模型准入 + 额度余量检查；通过返回记账上下文。"""
    plan = await store.get_effective_plan(user_id)
    quota_cents = plan.monthly_quota_cents if plan is not None else 0
    plan_id = plan.id if plan is not None else FREE_PLAN_ID

    if plan is not None and not model_allowed(plan, model):
        raise ProxyError(
            403,
            f"当前套餐（{plan.name}）不支持模型 {model}，升级后可使用",
            "permission_error",
            "model_not_allowed",
        )

    period_start = current_period_start()
    quota = await store.ensure_quota_period(user_id, period_start, quota_cents)
    if quota.used_cents >= quota.quota_cents:
        raise ProxyError(
            402,
            "本月平台额度已用尽，升级会员或绑定自有 API Key 可继续使用",
            "quota_exceeded",
            "quota_exceeded",
        )
    return QuotaContext(user_id=user_id, period_start=period_start, plan_id=plan_id)


async def charge_platform_quota(
    store: QuotaStore, ctx: QuotaContext, cents: int
) -> None:
    """调用完成后按实际成本记账；失败仅影响额度精度，不阻塞已下发响应。"""
    if cents <= 0:
        return
    new_used = await store.add_used_cents(ctx.user_id, ctx.period_start, cents)
    from eduagent_shared.logging import get_logger

    get_logger("proxy.quota").info(
        "platform_quota_charged",
        user_id=str(ctx.user_id),
        plan_id=ctx.plan_id,
        cost_cents=cents,
        used_cents=new_used,
    )


# ----------------------------------------------------------------------------
# 测试用内存实现
# ----------------------------------------------------------------------------


@dataclass
class QuotaPlanRecord:
    id: str
    name: str
    monthly_quota_cents: int
    allowed_models: list


@dataclass
class QuotaSubRecord:
    user_id: uuid.UUID
    plan_id: str
    status: str
    current_period_end: datetime


@dataclass
class QuotaPeriodRecord:
    user_id: uuid.UUID
    period_start: date
    quota_cents: int
    used_cents: int = 0


class InMemoryQuotaStore:
    """测试用内存实现（默认 seed free 档，与 init.sql 种子一致）。"""

    def __init__(self) -> None:
        self.plans: dict[str, QuotaPlanRecord] = {
            FREE_PLAN_ID: QuotaPlanRecord(
                id=FREE_PLAN_ID,
                name="免费版",
                monthly_quota_cents=300,
                allowed_models=[
                    "deepseek-chat",
                    "deepseek-reasoner",
                    "gpt-4o-mini",
                    "gemini-2.5-flash",
                    "gemini-1.5-flash",
                    "claude-3-5-haiku",
                ],
            )
        }
        self.subs: list[QuotaSubRecord] = []
        self.quotas: dict[tuple[uuid.UUID, date], QuotaPeriodRecord] = {}

    def seed_plan(
        self, plan_id: str, name: str, quota_cents: int, allowed_models: list
    ) -> None:
        self.plans[plan_id] = QuotaPlanRecord(plan_id, name, quota_cents, allowed_models)

    def seed_subscription(
        self, user_id: uuid.UUID, plan_id: str, period_end: datetime, status: str = "active"
    ) -> None:
        self.subs.append(QuotaSubRecord(user_id, plan_id, status, period_end))

    async def get_effective_plan(self, user_id: uuid.UUID) -> QuotaPlanRecord | None:
        now = datetime.now(timezone.utc)
        candidates = [
            s
            for s in self.subs
            if s.user_id == user_id and s.status == "active" and s.current_period_end > now
        ]
        if candidates:
            best = max(candidates, key=lambda s: s.current_period_end)
            return self.plans.get(best.plan_id)
        return self.plans.get(FREE_PLAN_ID)

    async def get_quota_period(
        self, user_id: uuid.UUID, period_start: date
    ) -> QuotaPeriodRecord | None:
        return self.quotas.get((user_id, period_start))

    async def ensure_quota_period(
        self, user_id: uuid.UUID, period_start: date, quota_cents: int
    ) -> QuotaPeriodRecord:
        key = (user_id, period_start)
        row = self.quotas.get(key)
        if row is None:
            row = QuotaPeriodRecord(user_id, period_start, quota_cents)
            self.quotas[key] = row
            return row
        if quota_cents > row.quota_cents:
            row.quota_cents = quota_cents
        return row

    async def add_used_cents(
        self, user_id: uuid.UUID, period_start: date, cents: int
    ) -> int:
        row = self.quotas[(user_id, period_start)]
        row.used_cents += cents
        return row.used_cents
