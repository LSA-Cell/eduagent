"""Cost Tracker 测试（§3.4）：台账写入 / 失败降级补写+告警 / 月度聚合 / 预算 deny。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.cost.ledger import CostRecorder, compute_cost_cents, monthly_usage
from app.cost.models import CostLedger, ModelPrice
from app.cost.pricing import PriceSnapshot, PricingReader, parse_route
from tests.conftest import FakeSession

USER = str(uuid.uuid4())


def _price_row(**overrides) -> ModelPrice:
    row = ModelPrice(
        provider="qwen", model="qwen-plus",
        input_per_1k_cents=100, output_per_1k_cents=200,  # 美分/1k tokens
        currency="USD", is_active=True,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


class TestPricing:
    async def test_price_read_from_db_no_hardcode(self) -> None:
        """单价完全来自 DB：改库行即改计费结果（代码内无单价字面量）。"""
        session = FakeSession()
        session.add(_price_row())
        await session.flush()
        reader = PricingReader(lambda: session, clock=lambda: 0.0)
        price = await reader.get_price("qwen", "qwen-plus")
        assert price is not None
        assert price.input_per_1k_cents == 100 and price.output_per_1k_cents == 200
        assert price.as_dict() == {"input_per_1k": 100, "output_per_1k": 200, "currency": "USD"}

    async def test_no_price_row_returns_none(self) -> None:
        reader = PricingReader(lambda: FakeSession())
        assert await reader.get_price("openai", "gpt-x") is None

    def test_parse_route(self) -> None:
        assert parse_route("qwen/qwen-plus") == ("qwen", "qwen-plus")
        assert parse_route("openai:gpt-x") == ("openai", "gpt-x")
        assert parse_route("default-model") == ("default", "default-model")
        assert parse_route(None) == ("default", "")

    def test_compute_cost_cents(self) -> None:
        snapshot = PriceSnapshot("qwen", "qwen-plus", 100, 200)
        # 2000 输入 * 100/1000 + 500 输出 * 200/1000 = 200 + 100 = 300 美分
        assert compute_cost_cents(snapshot, 2000, 500) == 300


class TestLedger:
    async def test_record_writes_ledger_with_price_snapshot(self) -> None:
        """台账行携带 DB 单价快照 + 折算成本（快照可溯源）。"""
        session = FakeSession()
        session.add(_price_row())
        await session.flush()
        recorder = CostRecorder(lambda: session, pricing=PricingReader(lambda: session))
        await recorder.record(
            run_id=str(uuid.uuid4()), user_id=USER, model_route="qwen/qwen-plus",
            input_tokens=2000, output_tokens=500, quota_tier="free",
        )
        [row] = [o for o in session.added if isinstance(o, CostLedger)]
        assert row.unit_price_snapshot == {
            "input_per_1k": 100, "output_per_1k": 200, "currency": "USD",
        }
        assert row.cost_cents == 300
        assert row.quota_tier == "free"

    async def test_no_price_records_tokens_only(self) -> None:
        """DB 无单价记录：跳过计费只记 tokens（cost_cents/快照为 NULL）。"""
        session = FakeSession()
        recorder = CostRecorder(lambda: session, pricing=PricingReader(lambda: session))
        await recorder.record(
            run_id=str(uuid.uuid4()), user_id=USER, model_route="unknown/model",
            input_tokens=100, output_tokens=50,
        )
        [row] = [o for o in session.added if isinstance(o, CostLedger)]
        assert row.cost_cents is None and row.unit_price_snapshot is None
        assert row.input_tokens == 100 and row.output_tokens == 50

    async def test_write_failure_degrades_to_retry_queue_and_alert(self) -> None:
        """写入失败：降级补写队列 + 告警，主流程不阻断（record 不抛）。"""
        session = FakeSession()
        session.add(_price_row())

        class _BrokenSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *exc): return False

            def add(self, obj): raise RuntimeError("db down")

        retry_queue: list = []
        alerts: list = []

        async def _pricing_ok(provider, model):
            return PriceSnapshot("qwen", "qwen-plus", 100, 200)

        recorder = CostRecorder(
            lambda: _BrokenSession(), retry_queue=retry_queue,
            on_alert=lambda msg, payload: alerts.append(msg),
        )
        recorder._pricing.get_price = _pricing_ok  # 绕过价格读取聚焦写入失败
        await recorder.record(
            run_id=str(uuid.uuid4()), user_id=USER, model_route="qwen/qwen-plus",
            input_tokens=10, output_tokens=5,
        )  # 不抛异常 = 主流程不阻断
        assert len(retry_queue) == 1 and retry_queue[0]["input_tokens"] == 10
        assert len(alerts) == 1


class TestMonthlyUsage:
    async def test_aggregates_current_month_only(self) -> None:
        session = FakeSession()
        now = datetime.now(timezone.utc)
        rows = [
            CostLedger(user_id=uuid.UUID(USER), input_tokens=100, output_tokens=50,
                       cache_hit_tokens=10, cost_cents=30),
            CostLedger(user_id=uuid.UUID(USER), input_tokens=200, output_tokens=100,
                       cost_cents=70),
            CostLedger(user_id=uuid.UUID(USER), input_tokens=999, output_tokens=999,
                       cost_cents=999, created_at=now.replace(day=1) - timedelta(days=1)),
        ]
        for row in rows:
            session.add(row)
        await session.flush()
        usage = await monthly_usage(session, USER, now=now)
        assert usage["runs"] == 2
        assert usage["input_tokens"] == 300 and usage["output_tokens"] == 150
        assert usage["cache_hit_tokens"] == 10
        assert usage["cost_cents"] == 100


class TestQuotaBudget:
    @staticmethod
    def _tool():
        from types import SimpleNamespace

        return SimpleNamespace(name="calculator")

    @staticmethod
    def _config(tier="free"):
        from types import SimpleNamespace

        return SimpleNamespace(user_id=USER, quota_tier=tier)

    async def test_over_budget_denies(self) -> None:
        """月度用量超预算 → quota 环节 deny（§3.4 与编排层配额联动）。"""
        from app.guardrails.quota import QuotaPolicy, QuotaStage

        policy = QuotaPolicy(monthly_budgets_cents={"free": 100})
        stage = QuotaStage(
            policy,
            monthly_usage_getter=lambda user_id: _usage(150),
        )
        decision = await stage.check(self._tool(), self._config(), {})
        assert decision.decision == "deny" and decision.stage == "quota"

    async def test_under_budget_allows(self) -> None:
        from app.guardrails.quota import QuotaPolicy, QuotaStage

        policy = QuotaPolicy(monthly_budgets_cents={"free": 100})
        stage = QuotaStage(policy, monthly_usage_getter=lambda user_id: _usage(50))
        decision = await stage.check(self._tool(), self._config(), {})
        assert decision.decision == "allow"

    async def test_default_policy_still_allows(self) -> None:
        """向后兼容：默认全 allow（无 policy / 无预算配置均放行）。"""
        from app.guardrails.quota import QuotaPolicy, QuotaStage

        assert (await QuotaStage().check(self._tool(), self._config(), {})).decision == "allow"
        assert (
            await QuotaStage(QuotaPolicy()).check(self._tool(), self._config(), {})
        ).decision == "allow"


async def _usage(cost_cents: int) -> dict:
    return {"cost_cents": cost_cents}
