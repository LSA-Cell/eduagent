"""分段 Checkpoint 观测（增强机制 §5.1）。

阶段枚举固定：slim / compact / memory_prefetch / model_first_token /
guardrail / tool_exec / docgen_wait / total。每 run 的 timings JSONB 落
agent_runs；p95 阈值配置化（TurnConfig.checkpoint_thresholds_ms），超阈值
经注入回调告警（deps.on_checkpoint_alert）。

红线：
- 埋点数据只含计时与计数，禁止携带消息内容/用户输入原文——
  CheckpointTimer 的 API 只接收阶段枚举名，不存在携带内容的入参；
- 埋点开销 <5ms/run——实现只调 time.monotonic() 与 dict 累加，无 I/O。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

# 固定阶段枚举（协议外阶段禁止打点）
PHASES = (
    "slim",
    "compact",
    "memory_prefetch",
    "model_first_token",
    "guardrail",
    "tool_exec",
    "docgen_wait",
    "total",
)

# p95 默认阈值（毫秒）；TurnConfig.checkpoint_thresholds_ms 可覆盖（配置化）
DEFAULT_THRESHOLDS_MS: dict[str, int] = {
    "slim": 50,
    "compact": 5_000,
    "memory_prefetch": 500,
    "model_first_token": 3_000,
    "guardrail": 500,
    "tool_exec": 60_000,
    "docgen_wait": 120_000,
    "total": 300_000,
}


class CheckpointTimer:
    """单 run 分段计时器（单调时钟；只累计毫秒数，拒绝任何内容载荷）。"""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._timings: dict[str, float] = {phase: 0.0 for phase in PHASES}
        self._open: dict[str, float] = {}

    def start(self, phase: str) -> None:
        """开始一个阶段计时（重复 start 覆盖起点，不构成嵌套语义）。"""
        if phase not in PHASES:
            raise ValueError(f"未知 checkpoint 阶段: {phase!r}（固定枚举 {PHASES}）")
        self._open[phase] = self._clock()

    def stop(self, phase: str) -> None:
        """结束阶段计时并累计（未 start 的 stop 静默忽略，埋点永不抛错）。"""
        started = self._open.pop(phase, None)
        if started is None:
            return
        self._timings[phase] += (self._clock() - started) * 1000.0

    @contextmanager
    def track(self, phase: str) -> Iterator[None]:
        """上下文管理器计时（with timer.track("slim"): ...）。"""
        self.start(phase)
        try:
            yield
        finally:
            self.stop(phase)

    def add_ms(self, phase: str, elapsed_ms: float) -> None:
        """直接累加外部测得的毫秒数（如模型首 token 延迟、docgen 轮询等待）。"""
        if phase not in PHASES:
            raise ValueError(f"未知 checkpoint 阶段: {phase!r}（固定枚举 {PHASES}）")
        self._timings[phase] += max(0.0, float(elapsed_ms))

    def as_dict(self) -> dict[str, int]:
        """timings 快照：{phase: ms}（整数毫秒，JSONB 落 agent_runs）。"""
        return {phase: int(round(ms)) for phase, ms in self._timings.items()}


def find_violations(
    timings: dict[str, Any],
    thresholds: Optional[dict[str, int]] = None,
) -> list[dict[str, Any]]:
    """超阈值检查：返回 [{phase, elapsed_ms, threshold_ms}]（无超阈为空表）。

    thresholds 为 None 时用 DEFAULT_THRESHOLDS_MS；配置只含部分阶段时
    未配置阶段不告警（显式配置语义，禁止隐式补默认造成告警风暴）。
    """
    limits = DEFAULT_THRESHOLDS_MS if thresholds is None else thresholds
    violations: list[dict[str, Any]] = []
    for phase, limit in limits.items():
        if phase not in PHASES:
            continue
        elapsed = int(timings.get(phase, 0) or 0)
        if elapsed > int(limit):
            violations.append({
                "phase": phase,
                "elapsed_ms": elapsed,
                "threshold_ms": int(limit),
            })
    violations.sort(key=lambda v: v["phase"])  # 确定性输出
    return violations


__all__ = [
    "DEFAULT_THRESHOLDS_MS",
    "PHASES",
    "CheckpointTimer",
    "find_violations",
]
