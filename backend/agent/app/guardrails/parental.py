"""环节 ④ 家长模式：未成年人触达开放类工具（web_search 等可配置清单）→ ask。

ask 由 Loop 推送 guardrail.ask 事件，确认回传通道见 Wave B 的 confirms API；
超时未确认按 deny 处理（执行器侧以"待确认"回注，不实际执行）。
"""

from __future__ import annotations

from typing import Any, FrozenSet, Iterable, Optional

from app.guardrails.decision import Decision

DEFAULT_OPEN_TOOLS: FrozenSet[str] = frozenset({"web_search", "sub_agent_dispatch"})


class ParentalStage:
    """未成年人 + 开放类工具 → ask；其余 allow。"""

    name = "parental"

    def __init__(self, open_tools: Optional[Iterable[str]] = None) -> None:
        self._open_tools = frozenset(open_tools) if open_tools is not None else DEFAULT_OPEN_TOOLS

    async def check(self, tool: Any, config: Any, arguments: dict[str, Any]) -> Decision:
        is_minor = bool(getattr(config, "is_minor", False))
        if is_minor and getattr(tool, "name", "") in self._open_tools:
            return Decision.ask(
                self.name,
                f"孩子正在使用开放类工具 {tool.name}，请家长确认是否允许",
            )
        return Decision.allow(self.name)
