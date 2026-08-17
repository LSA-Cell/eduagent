"""跨服务聊天契约（单一事实源）。

ChatMessage 原在 gateway / proxy / bff 三处各自定义（bff 名为 ChatMessageIn），
字段一致、各自漂移无防线。现收敛到共享包；各服务的请求模型
（ChatCompletionRequest / ProxyChatRequest 等）因服务职责不同仍各自持有，
但消息体一律引用本模块。

红线：消息结构变更只能改这里；服务内禁止重新定义 ChatMessage。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ChatMessage(BaseModel):
    """OpenAI 兼容聊天消息：content 为 str 或多模态分段数组。"""

    role: str
    content: Any  # str 或多模态分段数组（适配层仅提取 text 段）
    name: str | None = None
