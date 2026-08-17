"""Stop Hook 注册表与执行服务（增强机制 §2.5）。

- HookRegistry：注册 + 按上下文匹配（validator.applies）；
- run_matched_hooks：逐个执行，asyncio.wait_for 强制单校验器 ≤2s，
  超时/异常按失败处理（fail-safe：宁可强制继续也不放过未校验交付）；
- 审计落库由 Loop 侧完成（deps.audit.write_stop_hook），本模块不写任何表。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from app.stop_hooks.base import StopHookContext, StopHookResult, StopHookValidator

logger = logging.getLogger(__name__)

HOOK_TIMEOUT_S = 2.0  # 单校验器上限（规格硬性 ≤2s）
MAX_FORCED_CONTINUES = 2  # 同一 run 强制继续上限（第 3 次失败带 warning 交付）


@dataclass
class StopHookVerdict:
    """一次校验的执行结果（含超时/异常归一化）。"""

    hook_name: str
    passed: bool
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0


class HookRegistry:
    """校验器注册表（内置 3 个由 default_registry() 装载）。"""

    def __init__(self, validators: Optional[Iterable[StopHookValidator]] = None) -> None:
        self._validators: list[StopHookValidator] = list(validators or [])

    def register(self, validator: StopHookValidator) -> None:
        self._validators.append(validator)

    def match(self, ctx: StopHookContext) -> list[StopHookValidator]:
        """按当前上下文匹配适用校验器。"""
        matched: list[StopHookValidator] = []
        for validator in self._validators:
            try:
                if validator.applies(ctx):
                    matched.append(validator)
            except Exception:  # noqa: BLE001 - applies 异常视为不适用
                logger.warning("stop hook %s applies 异常", getattr(validator, "name", "?"), exc_info=True)
        return matched


async def run_matched_hooks(
    registry: HookRegistry,
    ctx: StopHookContext,
    *,
    timeout_s: float = HOOK_TIMEOUT_S,
) -> list[StopHookVerdict]:
    """执行匹配的校验器：单校验器 wait_for ≤2s，超时/异常按失败处理。"""
    verdicts: list[StopHookVerdict] = []
    for validator in registry.match(ctx):
        name = getattr(validator, "name", type(validator).__name__)
        started = time.monotonic()
        try:
            result: StopHookResult = await asyncio.wait_for(
                validator.validate(ctx), timeout=float(timeout_s)
            )
        except asyncio.TimeoutError:
            verdicts.append(StopHookVerdict(
                hook_name=name,
                passed=False,
                reason=f"质量校验器 {name} 超时（>{timeout_s:.0f}s），按未通过处理。",
                detail={"error": "timeout"},
                elapsed_ms=int((time.monotonic() - started) * 1000),
            ))
            continue
        except Exception as exc:  # noqa: BLE001 - 校验器异常按失败处理（fail-safe）
            verdicts.append(StopHookVerdict(
                hook_name=name,
                passed=False,
                reason=f"质量校验器 {name} 执行异常（{type(exc).__name__}），按未通过处理。",
                detail={"error": type(exc).__name__},
                elapsed_ms=int((time.monotonic() - started) * 1000),
            ))
            continue
        verdicts.append(StopHookVerdict(
            hook_name=name,
            passed=result.passed,
            reason=result.reason,
            detail=result.detail,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        ))
    return verdicts


def default_registry() -> HookRegistry:
    """内置校验器注册表（math_verify / grading_consistency / completeness_check）。"""
    from app.stop_hooks.builtins import (
        CompletenessCheckHook,
        GradingConsistencyHook,
        MathVerifyHook,
    )

    return HookRegistry([MathVerifyHook(), GradingConsistencyHook(), CompletenessCheckHook()])
