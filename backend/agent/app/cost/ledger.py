"""成本台账写入与月度聚合（§3.4）。

- CostRecorder.record：逐 run 落 cost_ledger；单价经 PricingReader 读取
  （无记录时 unit_price_snapshot/cost_cents 为 NULL，只记 tokens）；
- 写入失败降级为 retry_queue 补写 + on_alert 告警，函数本身永不抛异常
  （红线：禁止因记账失败阻断用户请求）；
- monthly_usage：用户当月聚合（护栏链第③环预算校验读取）。
"""

from __future__ import annotations

import inspect
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select

from app.cost.models import CostLedger
from app.cost.pricing import PriceSnapshot, PricingReader, parse_route

logger = logging.getLogger(__name__)

# 告警回调：(message: str, payload: dict) -> None 或 Awaitable[None]
AlertCallback = Callable[[str, dict[str, Any]], Any]


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return None


def compute_cost_cents(snapshot: PriceSnapshot, input_tokens: int, output_tokens: int) -> int:
    """按单价快照折算成本（美分，向下取整）。"""
    return (
        int(input_tokens) * snapshot.input_per_1k_cents
        + int(output_tokens) * snapshot.output_per_1k_cents
    ) // 1000


class CostRecorder:
    """逐 run 记账器（注入 session_factory / pricing / retry_queue / on_alert）。"""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        pricing: Optional[PricingReader] = None,
        retry_queue: Any = None,  # list 或 asyncio.Queue；补写 worker 消费（预留）
        on_alert: Optional[AlertCallback] = None,
    ) -> None:
        self._session_factory = session_factory
        self._pricing = pricing if pricing is not None else PricingReader(session_factory)
        self._retry_queue = retry_queue if retry_queue is not None else []
        self._on_alert = on_alert

    async def record(
        self,
        *,
        run_id: str,
        user_id: str,
        model_route: Optional[str],
        input_tokens: int,
        output_tokens: int,
        cache_hit_tokens: int = 0,
        quota_tier: Optional[str] = None,
    ) -> None:
        """落一条台账；任何失败都降级为补写队列 + 告警，禁止抛出。"""
        entry = {
            "run_id": run_id,
            "user_id": user_id,
            "model_route": model_route,
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "cache_hit_tokens": int(cache_hit_tokens),
            "quota_tier": quota_tier,
        }
        try:
            provider, model = parse_route(model_route)
            snapshot = await self._pricing.get_price(provider, model)
            await self._write(entry, snapshot)
        except Exception:  # noqa: BLE001 - 记账失败降级异步补写 + 告警
            logger.warning("cost_ledger 写入失败，降级补写队列（run %s）", run_id, exc_info=True)
            self._enqueue_retry(entry)
            await self._alert("cost_ledger 写入失败，已入补写队列", dict(entry))

    async def _write(self, entry: dict[str, Any], snapshot: Optional[PriceSnapshot]) -> None:
        row = CostLedger(
            run_id=_as_uuid(entry["run_id"]),
            user_id=_as_uuid(entry["user_id"]),
            model_route=entry["model_route"],
            input_tokens=entry["input_tokens"],
            output_tokens=entry["output_tokens"],
            cache_hit_tokens=entry["cache_hit_tokens"],
            unit_price_snapshot=snapshot.as_dict() if snapshot is not None else None,
            cost_cents=(
                compute_cost_cents(snapshot, entry["input_tokens"], entry["output_tokens"])
                if snapshot is not None
                else None
            ),
            quota_tier=entry.get("quota_tier"),
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.flush()
            await session.commit()

    def _enqueue_retry(self, entry: dict[str, Any]) -> None:
        """补写队列：asyncio.Queue 用 put_nowait，其余按 list.append 语义。"""
        try:
            queue = self._retry_queue
            put_nowait = getattr(queue, "put_nowait", None)
            if callable(put_nowait):
                put_nowait(entry)
            else:
                queue.append(entry)
        except Exception:  # noqa: BLE001 - 连补写都失败时只剩告警
            logger.error("cost_ledger 补写入队失败", exc_info=True)

    async def _alert(self, message: str, payload: dict[str, Any]) -> None:
        if self._on_alert is None:
            return
        try:
            result = self._on_alert(message, payload)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - 告警回调异常不继续扩散
            logger.warning("成本告警回调失败", exc_info=True)


async def monthly_usage(
    session: Any,
    user_id: str,
    *,
    now: Optional[datetime] = None,
) -> dict[str, int]:
    """聚合用户当月用量（护栏链 quota 环节预算比对用）。

    返回 {runs, input_tokens, output_tokens, cache_hit_tokens, cost_cents}；
    cost_cents 只累计已计价行（NULL 跳过）。
    """
    now = now or datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    uid = _as_uuid(user_id)
    result = await session.execute(
        select(CostLedger).where(
            CostLedger.user_id == uid, CostLedger.created_at >= month_start
        )
    )
    usage = {
        "runs": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_hit_tokens": 0,
        "cost_cents": 0,
    }
    for row in result.scalars().all():
        # Python 侧复核（兼容忽略 WHERE 的测试会话）
        if str(row.user_id) != str(uid):
            continue
        created = row.created_at
        if created is not None:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created < month_start:
                continue
        usage["runs"] += 1
        usage["input_tokens"] += int(row.input_tokens or 0)
        usage["output_tokens"] += int(row.output_tokens or 0)
        usage["cache_hit_tokens"] += int(row.cache_hit_tokens or 0)
        usage["cost_cents"] += int(row.cost_cents or 0)
    return usage


__all__ = ["AlertCallback", "CostRecorder", "compute_cost_cents", "monthly_usage"]
