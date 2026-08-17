"""价格表与额度折算（USD / 1M tokens → USD 美分）。

价格定期人工维护；model 按最长前缀匹配（如 gpt-4o-2024-08-06 → gpt-4o），
未命中的型号回落到该 provider 的 default 价。
used_budget 落库为整美分（UserApiKey.used_budget 为 BigInteger），
单次请求零头按四舍五入处理。
"""

from __future__ import annotations

# USD / 1M tokens
PRICE_TABLE: dict[str, dict[str, dict[str, float]]] = {
    "openai": {
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "default": {"input": 2.50, "output": 10.00},
    },
    "deepseek": {
        "deepseek-chat": {"input": 0.27, "output": 1.10},
        "deepseek-reasoner": {"input": 0.55, "output": 2.19},
        "default": {"input": 0.27, "output": 1.10},
    },
    "claude": {
        "claude-opus-4": {"input": 15.00, "output": 75.00},
        "claude-sonnet-4": {"input": 3.00, "output": 15.00},
        "claude-3-5-haiku": {"input": 0.80, "output": 4.00},
        "default": {"input": 3.00, "output": 15.00},
    },
    "gemini": {
        "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
        "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
        "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
        "default": {"input": 0.30, "output": 2.50},
    },
}

_FALLBACK_PRICE = {"input": 1.00, "output": 3.00}  # 未知 provider 兜底


def price_for(provider: str, model: str) -> dict[str, float]:
    """最长前缀匹配型号价格；未命中用 provider default，再兜底全局价。"""
    table = PRICE_TABLE.get(provider, {})
    matches = [k for k in table if k != "default" and model.startswith(k)]
    if matches:
        return table[max(matches, key=len)]
    return table.get("default", _FALLBACK_PRICE)


def cost_cents(
    provider: str, model: str, prompt_tokens: int, completion_tokens: int
) -> int:
    """按价格表折算单次调用的美分（四舍五入到整美分）。"""
    price = price_for(provider, model)
    usd = (prompt_tokens * price["input"] + completion_tokens * price["output"]) / 1_000_000
    return int(round(usd * 100))


def estimate_tokens(text: str) -> int:
    """上游未返回 usage 时的粗略估算：约 4 字符 / token。"""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)
