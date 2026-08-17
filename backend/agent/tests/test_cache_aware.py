"""Prompt-Cache 感知压缩测试（§3.1）：前缀改写拦截 / 破坏审计+告警 / 注入确定性。"""

from __future__ import annotations

import pytest

from app.context import cache_aware
from app.context.cache_aware import (
    CacheBreakWatcher,
    PrefixViolationError,
    checked_apply,
    guard_prefix,
    prefix_hash,
)
from app.memory import inject as memory_inject


def _msgs(system: str = "你是 EduAgent。") -> list[dict]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好呀"},
    ]


class TestPrefixHash:
    def test_same_input_same_hash(self) -> None:
        tools = [{"type": "function", "function": {"name": "calculator"}}]
        assert prefix_hash(_msgs(), tools) == prefix_hash(_msgs(), tools)

    def test_system_or_tools_change_changes_hash(self) -> None:
        base = prefix_hash(_msgs())
        assert prefix_hash(_msgs("你是 EduAgent！。")) != base
        tools = [{"type": "function", "function": {"name": "calculator"}}]
        assert prefix_hash(_msgs(), tools) != base

    def test_boundary_suffix_not_in_prefix(self) -> None:
        """前缀只含 messages[0] system + 工具定义；后缀消息变动不影响 hash。"""
        after = _msgs() + [{"role": "user", "content": "追加"}]
        assert prefix_hash(_msgs()) == prefix_hash(after)


class TestGuardPrefix:
    def test_compression_editing_prefix_rejected(self) -> None:
        """构造试图改写前缀的压缩结果：必须被检测拦截（抛 PrefixViolationError）。"""
        before = _msgs()
        after = [dict(before[0], content="被改写的 system"), *before[1:]]
        with pytest.raises(PrefixViolationError):
            guard_prefix(before, after, level="slim")

    def test_compression_after_boundary_allowed(self) -> None:
        """压缩边界之后的编辑放行（前缀不变）。"""
        before = _msgs()
        after = [before[0], {"role": "user", "content": "[已压缩]"}, before[2]]
        guard_prefix(before, after, level="microcompact")  # 不抛即通过


class TestCheckedApply:
    async def test_violation_refuses_apply_and_audits_and_alerts(self) -> None:
        """前缀被改写：拒绝应用（保留原消息）+ 落审计 + 触发告警回调。"""
        before = _msgs()
        after = [dict(before[0], content="改写"), *before[1:]]
        audits: list[dict] = []
        alerts: list[dict] = []

        applied = await checked_apply(
            before, after, level="autocompact",
            audit=lambda record: audits.append(record),
            on_cache_break=lambda info: alerts.append(info),
        )

        assert applied is before  # 拒绝应用
        assert len(audits) == 1 and audits[0]["level"] == "cache_break"
        assert "autocompact" in audits[0]["summary"]
        assert len(alerts) == 1 and alerts[0]["source"] == "autocompact"

    async def test_clean_compression_applies(self) -> None:
        before = _msgs()
        after = [before[0], before[1]]
        applied = await checked_apply(before, after, level="slim")
        assert applied is after


class TestCacheBreakWatcher:
    async def test_hash_break_triggers_audit_and_alert(self) -> None:
        """每请求前对比上一次前缀 hash：破坏即落审计 + 告警（含来源定位）。"""
        audits: list[dict] = []
        alerts: list[dict] = []
        watcher = CacheBreakWatcher(
            audit=lambda record: audits.append(record),
            on_cache_break=lambda info: alerts.append(info),
            session_id="s1", run_id="r1",
        )
        assert await watcher.observe(_msgs(), source="turn_1_request") is False  # 基线
        assert await watcher.observe(_msgs(), source="turn_2_request") is False  # 稳定
        broken = _msgs("被改写的 system")
        assert await watcher.observe(broken, source="turn_3_request") is True
        assert len(audits) == 1 and audits[0]["level"] == "cache_break"
        assert "turn_3_request" in audits[0]["summary"]
        assert len(alerts) == 1 and alerts[0]["source"] == "turn_3_request"

    async def test_no_alert_when_stable(self) -> None:
        alerts: list[dict] = []
        watcher = CacheBreakWatcher(on_cache_break=lambda info: alerts.append(info))
        for i in range(3):
            await watcher.observe(_msgs(), source=f"turn_{i}")
        assert alerts == []


class TestMemoryInjectDeterminism:
    def test_same_input_byte_identical(self) -> None:
        """同输入两次输出字节一致（前缀禁止逐字变动）。"""
        items = [
            {"id": "m1", "content": "目标：中考数学 110+", "memory_type": "preference", "importance": 0.9},
            {"id": "m2", "content": "薄弱：二次函数", "memory_type": "weakness", "importance": 0.8},
        ]
        block1, ids1 = memory_inject.format_memory_block(items)
        block2, ids2 = memory_inject.format_memory_block(items)
        assert block1 == block2 and ids1 == ids2

    def test_shuffled_input_same_output(self) -> None:
        """排序规则固定：相同集合任意输入顺序输出一致（含同分 tiebreaker）。"""
        items = [
            {"id": "m1", "content": "薄弱：二次函数", "memory_type": "weakness", "importance": 0.5},
            {"id": "m2", "content": "薄弱：因式分解", "memory_type": "weakness", "importance": 0.5},
            {"id": "m3", "content": "目标：期末 90+", "memory_type": "preference", "importance": 0.5},
        ]
        block1, ids1 = memory_inject.format_memory_block(items)
        block2, ids2 = memory_inject.format_memory_block(list(reversed(items)))
        assert block1 == block2 and ids1 == ids2
        # 优先级固定：preference 在 weakness 前
        assert block1.index("期末") < block1.index("二次函数")


class TestLoopPrefixIntegration:
    async def test_normal_run_has_no_cache_break(self) -> None:
        """正常 Loop 全程前缀稳定：不触发破坏审计/告警。"""
        from types import SimpleNamespace

        from app.loop.agent_loop import edu_agent_loop
        from tests.orchestration_fakes import FakeDeps, ScriptedGateway

        alerts: list[dict] = []
        gateway = ScriptedGateway()
        deps = FakeDeps(gateway=gateway)
        deps.on_cache_break = lambda info: alerts.append(info)

        req = SimpleNamespace(user_id="u1", message="你好", session_id=None,
                              subject="math", model=None)
        events = [event async for event in edu_agent_loop(req, deps)]
        assert events[-1].event == "run.end"
        assert alerts == []
        assert all(c.get("level") != "cache_break" for c in deps.audit.compactions)
