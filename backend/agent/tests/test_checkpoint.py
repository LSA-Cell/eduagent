"""分段 Checkpoint 观测测试（§5.1）。

覆盖红线：8 阶段固定枚举计时落 agent_runs.timings / 埋点开销 <5ms/run /
埋点只含计时计数、禁止携带消息内容 / p95 阈值配置化与告警钩子。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.observe.checkpoint import (
    DEFAULT_THRESHOLDS_MS,
    PHASES,
    CheckpointTimer,
    find_violations,
)


class TestCheckpointTimer:
    def test_phases_fixed_enum(self) -> None:
        """阶段枚举固定（协议外阶段打点直接拒绝）。"""
        assert PHASES == (
            "slim", "compact", "memory_prefetch", "model_first_token",
            "guardrail", "tool_exec", "docgen_wait", "total",
        )
        timer = CheckpointTimer()
        with pytest.raises(ValueError):
            timer.start("not_a_phase")
        with pytest.raises(ValueError):
            timer.add_ms("not_a_phase", 1.0)

    def test_track_and_add_ms(self) -> None:
        timer = CheckpointTimer()
        with timer.track("slim"):
            pass
        timer.add_ms("docgen_wait", 12.6)
        timings = timer.as_dict()
        assert set(timings) == set(PHASES)  # 8 阶段全量在（未发生的为 0）
        assert all(isinstance(v, int) and v >= 0 for v in timings.values())
        assert timings["docgen_wait"] == 13  # 四舍五入到整数毫秒

    def test_overhead_under_budget(self) -> None:
        """红线：埋点开销 <5ms/run——1000 次 track 循环宽松断言 <50ms。"""
        timer = CheckpointTimer()
        start = time.monotonic()
        for _ in range(1000):
            with timer.track("tool_exec"):
                pass
        elapsed_ms = (time.monotonic() - start) * 1000.0
        assert elapsed_ms < 50.0  # 实测量级 ~1ms，留 50x 余量

    def test_stop_without_start_is_noop(self) -> None:
        timer = CheckpointTimer()
        timer.stop("total")  # 未 start 的 stop 静默忽略（埋点永不抛错）
        assert timer.as_dict()["total"] == 0


class TestThresholds:
    def test_find_violations(self) -> None:
        timings = {"total": 301_000, "slim": 10}
        violations = find_violations(timings)  # 默认阈值
        assert violations == [
            {"phase": "total", "elapsed_ms": 301_000,
             "threshold_ms": DEFAULT_THRESHOLDS_MS["total"]}
        ]
        # 配置化：自定义阈值（只告警配置了的阶段）
        assert find_violations(timings, {"slim": 5}) == [
            {"phase": "slim", "elapsed_ms": 10, "threshold_ms": 5}
        ]
        assert find_violations(timings, {}) == []  # 空配置 = 不告警


class TestLoopIntegration:
    async def test_timings_landed_in_agent_runs(self) -> None:
        """Loop 全链路：8 阶段 timings 随 run 结束落 agent_runs（审计 sink）。"""
        from app.loop.agent_loop import edu_agent_loop
        from app.loop.deps_protocol import GatewayEvent
        from tests.orchestration_fakes import (
            FakeDeps,
            ScriptedGateway,
            make_registry,
            make_tool,
            tool_call,
        )

        # 一轮工具调用 + 一轮纯文本，覆盖 guardrail/tool_exec 计时
        gateway = ScriptedGateway(stream_scripts=[
            [GatewayEvent(finish_reason="tool_calls",
                          tool_calls=[tool_call("echo_tool", {"text": "hi"})])],
            [GatewayEvent(text="答案", finish_reason="stop")],
        ])
        deps = FakeDeps(gateway=gateway, registry=make_registry(make_tool()))
        secret_message = "用户隐私原文-哨兵-7f3a9c"
        req = SimpleNamespace(user_id="u1", message=secret_message, session_id=None,
                              subject="math", model=None)
        events = [e async for e in edu_agent_loop(req, deps)]
        assert events[-1].event == "run.end"

        final_runs = [r for r in deps.audit.runs if r["status"] != "running"]
        assert len(final_runs) == 1
        timings = final_runs[0]["timings"]
        assert timings is not None
        assert set(timings) == set(PHASES)  # 8 阶段枚举计时落库
        assert all(isinstance(v, int) and v >= 0 for v in timings.values())
        assert timings["tool_exec"] >= 0 and timings["guardrail"] >= 0
        assert timings["total"] >= timings["tool_exec"] >= 0
        # 红线：埋点只含计时计数，禁止携带消息内容/用户原文
        assert secret_message not in str(timings)
        assert all(type(v) is int for v in timings.values())  # 纯整数毫秒，无字符串载荷

    async def test_threshold_alert_hook(self) -> None:
        """p95 超阈值 → deps.on_checkpoint_alert 回调（配置进 TurnConfig）。"""
        from app.loop.agent_loop import edu_agent_loop
        from app.loop.deps_protocol import GatewayEvent
        from tests.orchestration_fakes import FakeDeps, ScriptedGateway

        deps = FakeDeps(gateway=ScriptedGateway(
            default_script=[GatewayEvent(text="答", finish_reason="stop")]
        ))
        alerts: list[dict] = []
        deps.on_checkpoint_alert = alerts.append
        req = SimpleNamespace(
            user_id="u1", message="你好", session_id=None, subject=None, model=None,
            checkpoint_thresholds_ms={"total": -1},  # total≥0 必超阈（离线 run 可 <1ms）
        )
        [e async for e in edu_agent_loop(req, deps)]
        assert len(alerts) == 1
        assert alerts[0]["violations"][0]["phase"] == "total"
        assert "run_id" in alerts[0] and alerts[0]["user_id"] == "u1"

    async def test_alert_payload_carries_no_message_content(self) -> None:
        """告警载荷只含计时计数（phase/elapsed/threshold + run/user id）。"""
        from app.loop.agent_loop import edu_agent_loop
        from app.loop.deps_protocol import GatewayEvent
        from tests.orchestration_fakes import FakeDeps, ScriptedGateway

        deps = FakeDeps(gateway=ScriptedGateway(
            default_script=[GatewayEvent(text="答", finish_reason="stop")]
        ))
        alerts: list[dict] = []
        deps.on_checkpoint_alert = alerts.append
        marker = "隐私哨兵-消息内容-b2c4d6"
        req = SimpleNamespace(
            user_id="u1", message=marker, session_id=None, subject=None, model=None,
            checkpoint_thresholds_ms={"total": -1, "slim": -1, "tool_exec": -1},
        )
        [e async for e in edu_agent_loop(req, deps)]
        assert alerts
        assert marker not in str(alerts)
