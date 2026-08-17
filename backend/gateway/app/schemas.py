"""请求模型：OpenAI 兼容 + 教育扩展字段。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# 透传给 vLLM 的 OpenAI 白名单字段（扩展字段一律剥离）
_OPENAI_PASSTHROUGH = (
    "temperature",
    "top_p",
    "max_tokens",
    "stop",
    "presence_penalty",
    "frequency_penalty",
)


class ChatMessage(BaseModel):
    role: str
    content: Any  # str 或多模态分段数组
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    """POST /v1/chat/completions 入参。"""

    model: str | None = None  # 缺省走默认自有模型
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: list[str] | str | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None

    # ---------- EduAgent 扩展字段（不透传给 vLLM） ----------
    subject: str | None = None  # 学科路由：math / physics / chemistry ...
    inference_source: Literal["auto", "internal", "external"] = "auto"
    user_id: str | None = None  # 由 BFF 注入；external 通道与额度追踪必需
    provider: str | None = None  # external 通道的厂商，默认 openai
    auto_failover: bool = True  # 自有模型失败时是否允许降级到 external

    def to_openai_body(self, model: str) -> dict[str, Any]:
        """生成透传给 vLLM 的 OpenAI 兼容请求体。"""
        body: dict[str, Any] = {
            "model": model,
            "messages": [m.model_dump(exclude_none=True) for m in self.messages],
            "stream": self.stream,
        }
        for key in _OPENAI_PASSTHROUGH:
            value = getattr(self, key)
            if value is not None:
                body[key] = value
        return body
