"""Stop Hook 校验器协议与只读上下文。

红线：校验器只许读上下文与沙箱计算，禁止写业务表 —— 上下文刻意不携带
session/deps，只给文本与卡片的拷贝；执行超时由 service 层 wait_for(2s) 强制。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class StopHookContext:
    """校验器只读上下文（无 I/O 句柄，禁止写业务表的结构性保证）。"""

    answer_text: str  # 本 turn 模型完整输出文本
    cards: tuple[dict[str, Any], ...] = ()  # 本 run 已产出卡片（card_type/card_data）
    subject: Optional[str] = None
    skill_code: Optional[str] = None
    role: str = "student"
    user_text: str = ""  # 用户最近一条消息（方程/题面来源）

    def combined_text(self) -> str:
        """答案文本 + 卡片 JSON（完整性关键词检索用）。"""
        parts = [self.answer_text]
        for card in self.cards:
            try:
                parts.append(json.dumps(card, ensure_ascii=False, default=str))
            except Exception:  # noqa: BLE001 - 不可序列化的卡片跳过
                continue
        return "\n".join(parts)


@dataclass(frozen=True)
class StopHookResult:
    passed: bool
    reason: str = ""  # 失败原因（注入 Loop 的 blocking 消息素材）
    detail: dict[str, Any] = field(default_factory=dict)  # 复核数据（落审计）


@runtime_checkable
class StopHookValidator(Protocol):
    """校验器协议：确定性代码实现，单次 ≤2s，纯计算无副作用。"""

    name: str

    def applies(self, ctx: StopHookContext) -> bool:
        """当前上下文是否适用本校验器。"""
        ...

    async def validate(self, ctx: StopHookContext) -> StopHookResult:
        """执行校验（只读 ctx；禁止任何 I/O 与业务表写入）。"""
        ...
