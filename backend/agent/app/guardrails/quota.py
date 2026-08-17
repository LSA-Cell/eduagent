"""环节 ③ 配额校验：按身份档（免费/付费/教师）+ 工具维度限额。

配额参数经 QuotaPolicy 注入，默认全 allow；用量由注入的
usage_getter(user_id, tool_name) 提供（Wave A 默认返回 0，即不限量）。

Wave D1（§3.4）：可选注入月度预算比对——QuotaPolicy.monthly_budgets_cents
配置身份档月度预算（美分），monthly_usage_getter(user_id) 返回
app.cost.ledger.monthly_usage 的聚合结果；超预算 deny。两者缺省（None）
时保持默认全 allow 的向后兼容。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Optional

from app.guardrails.decision import Decision

UsageGetter = Callable[[str, str], Awaitable[int]]
# 月度用量查询：user_id → monthly_usage() 聚合 dict（含 cost_cents 键）
MonthlyUsageGetter = Callable[[str], Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True)
class QuotaPolicy:
    """身份档配额：tier_limits[tier][tool_name] = 每会话调用上限。

    monthly_budgets_cents[tier] = 月度预算（美分，§3.4 成本台账联动）。
    """

    tier_limits: dict[str, dict[str, int]] = field(default_factory=dict)
    monthly_budgets_cents: dict[str, int] = field(default_factory=dict)

    def limit_for(self, tier: str, tool_name: str) -> Optional[int]:
        return self.tier_limits.get(tier, {}).get(tool_name)

    def budget_for(self, tier: str) -> Optional[int]:
        return self.monthly_budgets_cents.get(tier)


async def _zero_usage(user_id: str, tool_name: str) -> int:
    return 0


class QuotaStage:
    """默认全 allow；配置了限额且用量达上限时 deny；配置了月度预算且超支时 deny。"""

    name = "quota"

    def __init__(
        self,
        policy: Optional[QuotaPolicy] = None,
        usage_getter: Optional[UsageGetter] = None,
        monthly_usage_getter: Optional[MonthlyUsageGetter] = None,
    ) -> None:
        self._policy = policy
        self._usage_getter = usage_getter or _zero_usage
        self._monthly_usage_getter = monthly_usage_getter

    async def check(self, tool: Any, config: Any, arguments: dict[str, Any]) -> Decision:
        if self._policy is None:
            return Decision.allow(self.name)
        tier = getattr(config, "quota_tier", "free") or "free"
        limit = self._policy.limit_for(tier, getattr(tool, "name", ""))
        if limit is not None:
            used = await self._usage_getter(getattr(config, "user_id", ""), tool.name)
            if used >= limit:
                return Decision.deny(
                    self.name, f"工具 {tool.name} 已超出 {tier} 档配额（{used}/{limit}）"
                )
        budget = self._policy.budget_for(tier)
        if budget is not None and self._monthly_usage_getter is not None:
            usage = await self._monthly_usage_getter(getattr(config, "user_id", ""))
            spent = int(usage.get("cost_cents") or 0)
            if spent >= budget:
                return Decision.deny(
                    self.name,
                    f"{tier} 档本月用量已达预算上限（已用 {spent} 美分），"
                    "请升级身份档或下月再试",
                )
        return Decision.allow(self.name)
