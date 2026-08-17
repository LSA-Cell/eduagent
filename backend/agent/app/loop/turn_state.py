"""TurnState：逐轮可变的 Loop 运行状态。

红线：普通可变 dataclass，但禁止跨请求共享实例（每次 Loop 新建），
无全局可变 State（防止多用户串数据）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TurnState:
    """单次 Loop 运行的可变状态（请求级，随 Loop 结束销毁）。"""

    run_id: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    turn_count: int = 0
    tokens_used: int = 0  # 粗估累计（token_budget 熔断用）
    current_turn_start: int = 0  # 当前 turn 消息起始下标（压缩保护清单用）

    # 压缩追踪（每次压缩一条审计 dict：level/tokens_before/tokens_after/summary）
    compactions: list[dict[str, Any]] = field(default_factory=list)
    reactive_compact_used: bool = False  # L4 每 turn 仅 1 次机会

    # 记忆注入
    memory_injected: bool = False
    memory_ids: list[str] = field(default_factory=list)

    # 待回执卡片（card 事件随 SSE 推送后在此留痕）
    pending_cards: list[dict[str, Any]] = field(default_factory=list)

    # 同类失败连续计数（熔断用）：kind 变化时清零重计
    failure_kind: Optional[str] = None
    consecutive_failures: int = 0

    tool_calls_total: int = 0

    # ---- Wave C：交互协作机制运行状态 ---------------------------------------
    # ask_user_question 频次约束（每 run ≤2 次；禁止连续两 turn 都 ask）
    ask_count: int = 0
    ask_turns: list[int] = field(default_factory=list)
    # AgentTodo 快照（随 agent_runs.todo_snapshot 落库；card_gate 交付闸口用）
    todo_items: list[dict[str, Any]] = field(default_factory=list)
    todo_marker_tool_calls: int = 0  # 上次 todo 更新时的 tool_calls_total（跳状态佐证）
    # Stop Hooks 强制继续计数（≤2 次，第 3 次失败带 warning 交付）
    forced_continues: int = 0
    # ---- Wave C：取消/打断标记（run.interrupted 与 interrupted=true 落库用）
    interrupted: bool = False
    cancelled: bool = False  # 敏感决策 ask 超时按取消

    # ---- Wave D1：工具动态挂载（§4.3） ------------------------------------
    # tool_search 命中后写入：当轮有效（下一个模型请求携带其 Schema），
    # mounted_turn 之后的轮次开始前自动卸载（禁止常驻化规避上下文控制）
    mounted_tools: list[str] = field(default_factory=list)
    mounted_turn: Optional[int] = None

    def record_failure(self, kind: str) -> int:
        """记录一次失败，返回当前同类连续失败次数。"""
        if self.failure_kind == kind:
            self.consecutive_failures += 1
        else:
            self.failure_kind = kind
            self.consecutive_failures = 1
        return self.consecutive_failures

    def reset_failures(self) -> None:
        self.failure_kind = None
        self.consecutive_failures = 0
