"""护栏决策链：moderation → role → quota → parental 依次执行，短路返回。

- 每个环节的决策（含 allow）经审计回调落 guardrail_decisions（fail-soft）；
- 链只看工具元数据 + TurnConfig + 入参，不依赖工具内部状态；
- 顺序固定，禁止重排（设计文档 §3.6）。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Iterable, Optional

from app.guardrails.decision import Decision
from app.guardrails.moderation import ModerationStage
from app.guardrails.parental import ParentalStage
from app.guardrails.quota import QuotaPolicy, QuotaStage, UsageGetter
from app.guardrails.role import RoleStage

logger = logging.getLogger(__name__)

# 决策审计回调：({stage, decision, reason, tool_name, user_id, run_id})，fail-soft
DecisionAudit = Callable[[dict[str, Any]], Awaitable[None]]


class GuardrailChain:
    """四环节决策链。deps 提供 moderate()；audit 回调落审计（可为 None）。"""

    def __init__(
        self,
        deps: Any,
        *,
        quota_policy: Optional[QuotaPolicy] = None,
        usage_getter: Optional[UsageGetter] = None,
        open_tools: Optional[Iterable[str]] = None,
        audit: Optional[DecisionAudit] = None,
    ) -> None:
        self._stages = [
            ModerationStage(deps),
            RoleStage(),
            QuotaStage(quota_policy, usage_getter),
            ParentalStage(open_tools),
        ]
        self._audit = audit

    @property
    def stage_names(self) -> list[str]:
        return [s.name for s in self._stages]

    async def check(
        self,
        tool: Any,
        config: Any,
        arguments: dict[str, Any],
        *,
        run_id: Optional[str] = None,
    ) -> Decision:
        """依次过链，任一环节非 allow 即短路；逐环节落审计。"""
        final = Decision.allow()
        for stage in self._stages:
            decision = await stage.check(tool, config, arguments or {})
            await self._audit_decision(decision, tool, config, run_id)
            if decision.decision != "allow":
                return decision
            final = decision
        return final

    async def _audit_decision(
        self, decision: Decision, tool: Any, config: Any, run_id: Optional[str]
    ) -> None:
        if self._audit is None:
            return
        try:
            await self._audit({
                "user_id": getattr(config, "user_id", None),
                "run_id": run_id,
                "tool_name": getattr(tool, "name", ""),
                "stage": decision.stage,
                "decision": decision.decision,
                "reason": decision.reason,
            })
        except Exception:  # noqa: BLE001 - 审计回调异常仅记日志
            logger.warning("护栏决策审计回调失败", exc_info=True)


__all__ = ["Decision", "GuardrailChain", "QuotaPolicy"]
