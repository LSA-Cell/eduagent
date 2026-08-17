"""护栏 ask 的挂起确认存储（Wave B 确认闭环）。

取舍说明（确认闭环方案）：
    SSE 长连接内"Loop 真挂起等确认"需要跨请求共享协程/Future，且连接一旦断开
    挂起态即泄漏。本实现采用**幂等重发方案**（规格允许的最小闭环）：
      1. 工具调用被判 ask → 执行器在本 store 登记一条待确认记录，推送
         ``guardrail.ask`` 事件（含 confirm_token），本轮按"待确认"回注；
      2. 客户端调 ``POST /v1/guardrails/confirms``（confirm_token + approved）
         确认或拒绝；
      3. 已确认后客户端**重发原对话请求**并携带 confirm_token；模型再次发起
         同一工具调用时，执行器 ``consume`` 该令牌一次性放行（校验 user 与
         tool_name 匹配，消费即失效，防止重放）；
      4. 记录 120s 未确认自动过期，按 deny 语义处理（confirm 端点返回
         expired，执行器侧永远得不到可消费令牌）。
    代价是确认后需多一次用户请求往返；收益是无跨请求协程状态、天然幂等、
    断线恢复简单（令牌可随请求任意重发直到过期）。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

CONFIRM_TTL_S = 120.0  # 超时按 deny 处理（设计文档 §3.6）


@dataclass
class PendingConfirm:
    """一条待确认的工具调用。"""

    token: str
    user_id: str
    tool_name: str
    arguments: dict[str, Any]
    question: str
    run_id: Optional[str] = None
    approved: Optional[bool] = None  # None=待确认 / True=已确认 / False=已拒绝
    expires_at: float = 0.0


class PendingConfirmStore:
    """进程内存实现（单副本服务足够；多副本部署时换 Redis 实现同一接口）。"""

    def __init__(self, ttl_s: float = CONFIRM_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._items: dict[str, PendingConfirm] = {}

    def _purge(self) -> None:
        now = time.monotonic()
        expired = [t for t, item in self._items.items() if item.expires_at <= now]
        for token in expired:
            del self._items[token]

    def register(
        self,
        *,
        user_id: str,
        tool_name: str,
        arguments: Optional[dict[str, Any]] = None,
        question: str = "",
        run_id: Optional[str] = None,
    ) -> str:
        """登记一条待确认调用，返回 confirm_token。"""
        self._purge()
        token = uuid.uuid4().hex
        self._items[token] = PendingConfirm(
            token=token,
            user_id=str(user_id),
            tool_name=tool_name,
            arguments=dict(arguments or {}),
            question=question,
            run_id=run_id,
            expires_at=time.monotonic() + self._ttl_s,
        )
        return token

    def resolve(self, token: str, *, user_id: str) -> Optional[PendingConfirm]:
        """按 token 取记录（校验归属、剔除过期）；不存在/过期返回 None。"""
        self._purge()
        item = self._items.get(token)
        if item is None or item.user_id != str(user_id):
            return None
        return item

    def decide(self, token: str, *, user_id: str, approved: bool) -> Optional[PendingConfirm]:
        """确认/拒绝；拒绝立即删除（终态），确认保留待 consume。过期返回 None。"""
        item = self.resolve(token, user_id=user_id)
        if item is None:
            return None
        item.approved = approved
        if not approved:
            del self._items[token]
        return item

    def consume(self, token: str, *, user_id: str, tool_name: str) -> bool:
        """一次性放行校验：已确认 + 归属与工具名匹配 → 删除并返回 True。"""
        item = self.resolve(token, user_id=user_id)
        if item is None or item.approved is not True:
            return False
        if item.tool_name != tool_name:
            return False  # 令牌与工具不匹配：不消费，也不放行
        del self._items[token]
        return True
