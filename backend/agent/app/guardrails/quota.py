"""环节 ③ 配额校验：按身份档（免费/付费/教师）+ 工具维度限额。

配额参数经 QuotaPolicy 注入，默认全 allow；用量由注入的
usage_getter(user_id, tool_name) 提供（Wave A 默认返回 0，即不限量）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.guardrails.decision import Decision

UsageGetter = Callable[[str, str], Awaitable[int]]


@dataclass(frozen=True)
class QuotaPolicy:
    """身份档配额：tier_limits[tier][tool_name] = 每会话调用上限。"""

    tier_limits: dict[str, dict[str, int]] = field(default_factory=dict)

    def limit_for(self, tier: str, tool_name: str) -> Optional[int]:
        return self.tier_limits.get(tier, {}).get(tool_name)


async def _zero_usage(user_id: str, tool_name: str) -> int:
    return 0


class QuotaStage:
    """默认全 allow；配置了限额且用量达上限时 deny。"""

    name = "quota"

    def __init__(
        self,
        policy: Optional[QuotaPolicy] = None,
        usage_getter: Optional[UsageGetter] = None,
    ) -> None:
        self._policy = policy
        self._usage_getter = usage_getter or _zero_usage

    async def check(self, tool: Any, config: Any, arguments: dict[str, Any]) -> Decision:
        if self._policy is None:
            return Decision.allow(self.name)
        tier = getattr(config, "quota_tier", "free") or "free"
        limit = self._policy.limit_for(tier, getattr(tool, "name", ""))
        if limit is None:
            return Decision.allow(self.name)
        used = await self._usage_getter(getattr(config, "user_id", ""), tool.name)
        if used >= limit:
            return Decision.deny(
                self.name, f"工具 {tool.name} 已超出 {tier} 档配额（{used}/{limit}）"
            )
        return Decision.allow(self.name)
