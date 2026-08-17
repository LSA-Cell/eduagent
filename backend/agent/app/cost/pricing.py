"""单价读取（§3.4）：model_prices 表按 provider+model 配置，进程内缓存 60s。

红线：单价禁止硬编码——本模块不含任何单价字面量；DB 无记录时返回
None，台账只记 tokens 不计费（unit_price_snapshot/cost_cents 为 NULL）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy import select

from app.cost.models import ModelPrice

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TTL_S = 60.0


def datetime_min():
    from datetime import datetime, timezone

    return datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class PriceSnapshot:
    """一次计费采用的单价快照（随台账行落库，对账可溯源）。"""

    provider: str
    model: str
    input_per_1k_cents: int  # 美分 / 1k tokens
    output_per_1k_cents: int
    currency: str = "USD"

    def as_dict(self) -> dict[str, Any]:
        """台账 unit_price_snapshot JSONB 契约（对齐 §七 DDL 注释）。"""
        return {
            "input_per_1k": self.input_per_1k_cents,
            "output_per_1k": self.output_per_1k_cents,
            "currency": self.currency,
        }


def parse_route(model_route: Optional[str]) -> tuple[str, str]:
    """model_route → (provider, model)。

    约定 "provider/model" 或 "provider:model"；无 provider 前缀时归
    "default" 档（单价表按 same key 配置即可命中）。
    """
    route = (model_route or "").strip()
    for sep in ("/", ":"):
        if sep in route:
            provider, _, model = route.partition(sep)
            return provider.strip() or "default", model.strip()
    return "default", route


class PricingReader:
    """单价读取器：注入 session_factory，进程内缓存 60s（含未命中缓存）。"""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        cache_ttl_s: float = DEFAULT_CACHE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._cache_ttl_s = cache_ttl_s
        self._clock = clock
        self._cache: dict[tuple[str, str], tuple[float, Optional[PriceSnapshot]]] = {}

    async def get_price(self, provider: str, model: str) -> Optional[PriceSnapshot]:
        """取最新生效的单价；DB 无记录或读取失败返回 None（不计费只记 tokens）。"""
        key = (provider, model)
        cached = self._cache.get(key)
        now = self._clock()
        if cached is not None and cached[0] > now:
            return cached[1]
        snapshot = await self._read(provider, model)
        self._cache[key] = (now + self._cache_ttl_s, snapshot)
        return snapshot

    async def _read(self, provider: str, model: str) -> Optional[PriceSnapshot]:
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(ModelPrice)
                    .where(
                        ModelPrice.provider == provider,
                        ModelPrice.model == model,
                        ModelPrice.is_active.is_(True),
                    )
                    .order_by(ModelPrice.effective_from.desc())
                )
                rows = [
                    row
                    for row in result.scalars().all()
                    if row.provider == provider and row.model == model and row.is_active
                ]
        except Exception:  # noqa: BLE001 - 单价读取失败降级为不计费
            logger.warning("model_prices 读取失败（%s/%s），本次只记 tokens", provider, model)
            return None
        if not rows:
            return None
        rows.sort(key=lambda row: row.effective_from or datetime_min(), reverse=True)
        row = rows[0]
        return PriceSnapshot(
            provider=row.provider,
            model=row.model,
            input_per_1k_cents=int(row.input_per_1k_cents),
            output_per_1k_cents=int(row.output_per_1k_cents),
            currency=str(row.currency or "USD"),
        )


__all__ = ["PriceSnapshot", "PricingReader", "parse_route", "DEFAULT_CACHE_TTL_S"]
