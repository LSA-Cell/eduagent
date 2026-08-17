"""Plan 状态机（增强机制 §2.1）。

状态：drafting → awaiting_confirm → confirmed → generating → completed；
旁路：cancelled / expired（awaiting_confirm 超时未操作 → expired）。
非法迁移抛 PlanStateError（代码级强制，杜绝跳过 confirmed 直接生成）。
"""

from __future__ import annotations

from enum import Enum


class PlanStatus(str, Enum):
    DRAFTING = "drafting"
    AWAITING_CONFIRM = "awaiting_confirm"
    CONFIRMED = "confirmed"
    GENERATING = "generating"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PlanStateError(RuntimeError):
    """非法状态迁移 / 状态前置校验失败。"""


# 允许的有向迁移（终态不可再迁移）
_ALLOWED: dict[PlanStatus, frozenset[PlanStatus]] = {
    PlanStatus.DRAFTING: frozenset({PlanStatus.AWAITING_CONFIRM, PlanStatus.CANCELLED}),
    PlanStatus.AWAITING_CONFIRM: frozenset(
        {PlanStatus.CONFIRMED, PlanStatus.CANCELLED, PlanStatus.EXPIRED}
    ),
    PlanStatus.CONFIRMED: frozenset({PlanStatus.GENERATING, PlanStatus.CANCELLED}),
    PlanStatus.GENERATING: frozenset({PlanStatus.COMPLETED, PlanStatus.CANCELLED}),
    PlanStatus.COMPLETED: frozenset(),
    PlanStatus.CANCELLED: frozenset(),
    PlanStatus.EXPIRED: frozenset(),
}


def ensure_transition(current: PlanStatus, target: PlanStatus) -> None:
    """校验迁移合法性；非法即抛 PlanStateError。"""
    if target not in _ALLOWED[current]:
        raise PlanStateError(f"非法 plan 状态迁移: {current.value} → {target.value}")


def transition(plan: object, target: PlanStatus) -> PlanStatus:
    """在 plan 行上执行迁移（就地改 status），返回旧状态。"""
    current = PlanStatus(str(getattr(plan, "status")))
    ensure_transition(current, target)
    setattr(plan, "status", target.value)
    return current
