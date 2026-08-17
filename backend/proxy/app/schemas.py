"""请求 / 响应模型（与推理网关 external 通道的调用契约对齐，见 gateway/app/failover.py）。

ChatMessage 统一引用共享契约 eduagent_shared.contracts（单一事实源）。
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from eduagent_shared.contracts import ChatMessage

SUPPORTED_PROVIDERS = ("openai", "deepseek", "claude", "gemini")


class ProxyChatRequest(BaseModel):
    """POST /proxy/chat 入参（由推理网关注入 user_id 后转发）。"""

    user_id: uuid.UUID
    provider: Literal["openai", "deepseek", "claude", "gemini"] = "openai"
    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    max_tokens: int | None = None


class ProviderUsageOut(BaseModel):
    """GET /proxy/usage/{user_id} 的单 provider 用量（不含任何 Key 信息）。"""

    provider: str
    monthly_budget: int | None  # USD 美分；None 表示不限额
    used_budget: int
    remaining_budget: int | None
    is_active: bool
