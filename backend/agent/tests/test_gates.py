"""Feature Gates 灰度测试（§5.2）。

覆盖红线：rollout 分桶确定性（同 user 同结果）/ allowlist 优先 /
默认 off（键不存在、DB 故障、缓存未就绪）/ 进程内缓存 60s。
"""

from __future__ import annotations

import uuid

import pytest

from app.gates.models import FeatureGate
from app.gates.registry import GateRegistry, bucket_of
from tests.conftest import FakeSession, as_factory


def _seed_gate(
    session: FakeSession,
    key: str,
    *,
    enabled: bool = True,
    rollout_pct: int = 100,
    allowlist: list | None = None,
) -> None:
    session.add(FeatureGate(key=key, enabled=enabled, rollout_pct=rollout_pct,
                            allowlist=allowlist or []))


class MutableClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TestBucketing:
    def test_bucket_deterministic(self) -> None:
        """同一 user 同一 key 分桶结果恒定（跨实例/跨进程一致）。"""
        uid = str(uuid.uuid4())
        assert bucket_of("suggestion_speculation", uid) == bucket_of("suggestion_speculation", uid)
        assert 0 <= bucket_of("k", uid) < 100

    async def test_same_user_same_result(self) -> None:
        """rollout 分桶确定性：同一用户反复判定结果一致。"""
        session = FakeSession()
        _seed_gate(session, "new_math_model", enabled=True, rollout_pct=50)
        await session.flush()
        registry = GateRegistry(as_factory(session))
        await registry.refresh()
        for uid in [str(uuid.uuid4()) for _ in range(20)]:
            first = registry.is_enabled("new_math_model", user_id=uid)
            for _ in range(5):
                assert registry.is_enabled("new_math_model", user_id=uid) == first
        # 50% 分桶确实产生了 on/off 两侧样本（离线确定性：由 sha256 保证）
        results = {registry.is_enabled("new_math_model", user_id=str(i)) for i in range(50)}
        assert results == {True, False}

    async def test_allowlist_takes_priority(self) -> None:
        """allowlist 优先：enabled=false / rollout=0 时白名单用户仍放行。"""
        session = FakeSession()
        vip = uuid.uuid4()
        _seed_gate(session, "teacher_tool", enabled=False, rollout_pct=0,
                   allowlist=[vip])
        await session.flush()
        registry = GateRegistry(as_factory(session))
        await registry.refresh()
        assert registry.is_enabled("teacher_tool", user_id=str(vip)) is True
        assert registry.is_enabled("teacher_tool", user_id=str(uuid.uuid4())) is False


class TestDefaultOff:
    async def test_unknown_key_off(self) -> None:
        """红线：键不存在默认 off。"""
        session = FakeSession()
        await session.flush()
        registry = GateRegistry(as_factory(session))
        await registry.refresh()
        assert registry.is_enabled("missing_gate", user_id="u1") is False

    def test_cold_cache_off(self) -> None:
        """红线：缓存未就绪（未 refresh）默认 off。"""
        session = FakeSession()
        _seed_gate(session, "g", enabled=True, rollout_pct=100)
        registry = GateRegistry(as_factory(session))  # 注意：未 refresh
        assert registry.is_enabled("g", user_id="u1") is False

    async def test_db_failure_after_ttl_fails_closed(self) -> None:
        """DB 故障且缓存过期 → fail-closed 默认 off（红线：DB 不可用默认 off）。"""
        session = FakeSession()
        _seed_gate(session, "g", enabled=True, rollout_pct=100)
        await session.flush()
        clock = MutableClock()
        registry = GateRegistry(as_factory(session), clock=clock)
        await registry.refresh()
        assert registry.is_enabled("g", user_id="u1") is True

        clock.now = 1000.0  # 越过 TTL
        registry._session_factory = lambda: _BrokenCM()
        await registry.refresh()  # 刷新失败（保留旧缓存但已过期）
        assert registry.is_enabled("g", user_id="u1") is False  # fail-closed

    async def test_db_failure_first_load_off(self) -> None:
        """首次加载即 DB 故障 → 默认 off。"""
        registry = GateRegistry(lambda: _BrokenCM())
        await registry.refresh()
        assert registry.is_enabled("g", user_id="u1") is False


class _BrokenCM:
    async def __aenter__(self):
        raise RuntimeError("db down")

    async def __aexit__(self, *exc):
        return False


class TestCacheTTL:
    async def test_cache_60s(self) -> None:
        """进程内缓存 60s：TTL 内不重复读库，越过 TTL 后 refresh 热生效。"""
        session = FakeSession()
        _seed_gate(session, "g1", enabled=True, rollout_pct=100)
        await session.flush()
        clock = MutableClock()
        registry = GateRegistry(as_factory(session), clock=clock)
        await registry.refresh()
        assert registry.is_enabled("g1", user_id="u1") is True

        # TTL 内新增 gate：不刷新 → 判定按旧缓存（g2 不可见 → off）
        _seed_gate(session, "g2", enabled=True, rollout_pct=100)
        await session.flush()
        clock.now = 30.0
        assert registry.stale is False
        assert registry.is_enabled("g2", user_id="u1") is False

        # 越过 TTL：refresh 后热生效
        clock.now = 61.0
        assert registry.stale is True
        assert registry.is_enabled("g2", user_id="u1") is False  # 过期未刷新仍 off
        await registry.refresh()
        assert registry.is_enabled("g2", user_id="u1") is True

    async def test_speculation_gate_end_to_end(self) -> None:
        """suggestion_speculation 既有钩子接上：默认 off；gate 开后走只读预取。"""
        from types import SimpleNamespace

        from app.suggest.generator import maybe_prefetch, speculation_enabled

        session = FakeSession()
        _seed_gate(session, "suggestion_speculation", enabled=True, rollout_pct=100)
        await session.flush()
        registry = GateRegistry(as_factory(session))

        async def rag_search(**kwargs):
            return ["知识点条目"]

        deps = SimpleNamespace(
            feature_gate=registry.is_enabled,
            feature_gate_refresh=registry.refresh,
            rag_search=rag_search,
        )
        assert speculation_enabled(deps, "u1") is False  # 缓存未就绪 → off
        result = await maybe_prefetch(deps, suggestions=["q1"], user_id="u1")
        assert result == [["知识点条目"]]  # refresh 后 gate on，只读预取生效
