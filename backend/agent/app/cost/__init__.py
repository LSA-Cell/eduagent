"""Cost Tracker 成本台账（增强机制 §3.4）。

红线：单价禁止硬编码（DB model_prices 配置 + 台账存历史快照）；
前端禁止暴露成本明细与单价（本模块数据只供配额/预算/对账）；
台账写入失败降级异步补写 + 告警，禁止阻断用户请求。
"""

from app.cost.ledger import CostRecorder, compute_cost_cents, monthly_usage
from app.cost.models import CostLedger, ModelPrice
from app.cost.pricing import PriceSnapshot, PricingReader, parse_route

__all__ = [
    "CostLedger",
    "CostRecorder",
    "ModelPrice",
    "PriceSnapshot",
    "PricingReader",
    "compute_cost_cents",
    "monthly_usage",
    "parse_route",
]
