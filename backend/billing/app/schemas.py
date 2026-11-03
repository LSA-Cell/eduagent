"""请求 / 响应模型（契约见 指导/后端设计/计费/spec.md §4）。"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SUPPORTED_CHANNELS = ("mock", "apple_iap", "google_play", "stripe", "alipay", "wechat")


class PlanOut(BaseModel):
    id: str
    name: str
    price_cents: int
    currency: str
    monthly_quota_cents: int
    allowed_models: list[str]
    features: dict[str, Any]
    sort_order: int


class QuotaOut(BaseModel):
    period_start: date
    quota_cents: int
    used_cents: int
    remaining_cents: int


class SubscriptionOut(BaseModel):
    """当前订阅 + 本期额度用量；无付费订阅时 plan 为 free。"""

    plan: PlanOut
    status: str  # active / free（无付费订阅）/ expired / canceled
    channel: str | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    auto_renew: bool = True
    quota: QuotaOut


class OrderCreate(BaseModel):
    plan_id: str = Field(min_length=1, max_length=30)
    channel: Literal["mock", "apple_iap", "google_play", "stripe", "alipay", "wechat"]


class OrderOut(BaseModel):
    id: uuid.UUID
    plan_id: str
    amount_cents: int
    currency: str
    channel: str
    status: str
    created_at: datetime
    paid_at: datetime | None = None
    # 渠道支付参数（mock 渠道返回可直接 confirm 的 mock_token）
    payment_params: dict[str, Any] = {}


class AdminGrantRequest(BaseModel):
    """手工开通/续期（客服与测试链路用；仅 internal token 可调）。"""

    user_id: uuid.UUID
    plan_id: str = Field(min_length=1, max_length=30)
    days: int = Field(default=30, ge=1, le=3660)
