"""Denial Tracking 测试（§3.3）：计数更新/清零/降频排序/默认关闭建议/不拦截主动发起。"""

from __future__ import annotations

import pytest

from app.denial.counters import DenialTracker, load_counters, record_denial, record_use
from app.denial.ranking import apply_denial_ranking, should_default_off
from tests.conftest import FakeSession


class TestCounters:
    async def test_record_denial_increments(self) -> None:
        session = FakeSession()
        for _ in range(3):
            await record_denial(session, "u1", "make_ppt")
        counters = await load_counters(session, "u1")
        assert counters["make_ppt"].consecutive == 3
        assert counters["make_ppt"].total == 3

    async def test_record_use_resets_consecutive_only(self) -> None:
        """主动使用一次 → consecutive 清零；total（累计口径）不回退。"""
        session = FakeSession()
        for _ in range(2):
            await record_denial(session, "u1", "flashcards")
        await record_use(session, "u1", "flashcards")
        counters = await load_counters(session, "u1")
        assert counters["flashcards"].consecutive == 0
        assert counters["flashcards"].total == 2

    async def test_counters_isolated_per_user(self) -> None:
        session = FakeSession()
        await record_denial(session, "u1", "web_search")
        assert await load_counters(session, "u2") == {}

    async def test_tracker_fail_soft(self) -> None:
        """DB 故障：fail-soft 不抛异常，读取降级为空计数。"""

        class _BrokenSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *exc): return False
            async def execute(self, *a, **k): raise RuntimeError("db down")

        tracker = DenialTracker(lambda: _BrokenSession())
        await tracker.denial("u1", "make_ppt")  # 不抛
        await tracker.use("u1", "make_ppt")  # 不抛
        assert await tracker.counters("u1") == {}


class TestRanking:
    @staticmethod
    def _items() -> list[dict]:
        return [{"code": code} for code in ("a", "b", "c", "d")]

    def test_consecutive_3_sinks_to_bottom(self) -> None:
        """consecutive ≥3 → 排序沉底（其余相对顺序不变）。"""
        counters = {"b": (3, 3)}
        ranked = apply_denial_ranking(self._items(), counters)
        assert [i["code"] for i in ranked] == ["a", "c", "d", "b"]
        assert ranked[-1]["denial_consecutive"] == 3

    def test_total_20_marks_default_off(self) -> None:
        """total ≥20 → default_enabled=False（默认关闭建议）。"""
        counters = {"c": (0, 20)}
        ranked = apply_denial_ranking(self._items(), counters)
        by_code = {i["code"]: i for i in ranked}
        assert by_code["c"]["default_enabled"] is False
        assert by_code["a"]["default_enabled"] is True
        assert should_default_off((0, 20)) is True
        assert should_default_off((5, 19)) is False

    def test_active_use_never_blocked(self) -> None:
        """红线：计数只影响排序/默认值——条目始终保留，禁止被移除或拦截。"""
        counters = {"b": (10, 50)}  # 远超双阈值
        ranked = apply_denial_ranking(self._items(), counters)
        assert len(ranked) == 4  # 条目一个不少
        assert "b" in [i["code"] for i in ranked]

    def test_no_counters_keeps_order(self) -> None:
        ranked = apply_denial_ranking(self._items(), {})
        assert [i["code"] for i in ranked] == ["a", "b", "c", "d"]
