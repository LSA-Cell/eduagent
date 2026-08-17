"""tool_search 工具与延迟加载配置（增强机制 §4.3）。

- 常驻工具集 ≤4（按角色配置，注册表启动校验超限拒绝）；其余工具
  deferred=true 不进初始 prompt；
- tool_search 为常驻元工具（不计入 ≤4 配额）：按 name+description
  关键词检索 deferred 工具，命中后当轮动态挂载其 Schema（写入
  TurnState.mounted_tools，Loop 在下轮模型请求携带，再下轮开始前
  自动卸载——挂载仅当轮有效，禁止常驻化）；
- 搜索索引按角色过滤构建：角色不可见工具不进索引（红线：学生搜不到
  教师工具）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from pydantic import BaseModel, Field

from app.tools.base import EduTool, EduToolBase, ToolContext, ToolResult
from app.tools.registry import TOOL_SEARCH_NAME, EduToolRegistry

logger = logging.getLogger(__name__)

SEARCH_RESULT_LIMIT = 5

# 默认常驻清单（§4.3 规格示例；均 ≤4，启动校验强制）
DEFAULT_RESIDENT_TOOLS: dict[str, list[str]] = {
    "student": ["ocr_extract", "calculator", "ask_user_question", "sub_agent_dispatch"],
    "teacher": ["sub_agent_dispatch", "ask_user_question", "ocr_extract", "calculator"],
    "parent": ["ask_user_question", "ocr_extract", "web_search"],
}


def build_search_index(registry: EduToolRegistry, role: str) -> list[EduTool]:
    """构建该角色的检索索引：仅 deferred 且角色可见的工具（角色过滤在此完成）。"""
    return [
        tool
        for tool in registry.visible_for(role)
        if getattr(tool, "deferred", False) and tool.name != TOOL_SEARCH_NAME
    ]


def search_deferred_tools(
    registry: EduToolRegistry,
    role: str,
    query: str,
    *,
    limit: int = SEARCH_RESULT_LIMIT,
) -> list[EduTool]:
    """按 name+description 关键词检索 deferred 工具（确定性排序）。"""
    terms = [term for term in query.lower().split() if term] or [query.lower()]
    terms = [t for t in terms if t]
    scored: list[tuple[int, str, EduTool]] = []
    for tool in build_search_index(registry, role):
        haystack = f"{tool.name} {tool.description}".lower()
        if terms and not any(term in haystack for term in terms):
            continue
        name_hit = any(term in tool.name.lower() for term in terms)
        scored.append((0 if name_hit else 1, tool.name, tool))
    scored.sort(key=lambda entry: (entry[0], entry[1]))  # 名称命中优先，其余按名排序
    return [tool for _, _, tool in scored[:limit]]


def configure_lazy_loading(
    registry: EduToolRegistry,
    resident_map: Optional[dict[str, Sequence[str]]] = None,
) -> None:
    """按角色配置常驻清单并把其余工具标记为 deferred（启动期调用）。

    常驻清单 >4 或含未注册工具时 set_resident_tools 抛 ToolError（拒绝启动）。
    """
    mapping = {role: list(names) for role, names in (resident_map or DEFAULT_RESIDENT_TOOLS).items()}
    keep = {TOOL_SEARCH_NAME}
    for names in mapping.values():
        keep.update(names)
    for name in registry.names():
        tool = registry.get(name)
        tool.deferred = name not in keep  # type: ignore[attr-defined]
    for role, names in mapping.items():
        registry.set_resident_tools(role, names)


class ToolSearchArgs(BaseModel):
    query: str = Field(min_length=1, description="检索关键词（工具名或能力描述）")


class ToolSearchTool(EduToolBase):
    """检索并临时挂载 deferred 工具（只读；命中仅当轮有效，下轮自动卸载）。"""

    name = TOOL_SEARCH_NAME
    description = "检索当前可用的更多工具（按名称/能力关键词），命中后本轮即可直接调用。"
    input_model = ToolSearchArgs
    is_read_only = True
    is_concurrency_safe = True
    timeout_s = 5

    async def call(self, args: ToolSearchArgs, ctx: ToolContext) -> ToolResult:
        """无写副作用：只读检索 + TurnState 当轮挂载标记（Loop 自动卸载）。"""
        registry = getattr(getattr(ctx, "deps", None), "tool_registry", None)
        if registry is None:
            return ToolResult(content="工具注册表不可用，无法检索。")
        role = getattr(ctx, "role", None) or "student"
        hits = search_deferred_tools(registry, role, args.query)
        if not hits:
            return ToolResult(
                content=f"未找到与「{args.query}」匹配的可用工具。",
                card_data={"query": args.query, "mounted": []},
            )
        names = [tool.name for tool in hits]
        # 当轮动态挂载：TurnState 记录，Loop 下一个模型请求携带其 Schema，
        # 再下轮开始前自动卸载（仅当轮有效，禁止常驻化）
        turn_state = getattr(ctx, "turn_state", None)
        if turn_state is not None:
            turn_state.mounted_tools = names
            turn_state.mounted_turn = turn_state.turn_count
        listing = "；".join(f"{tool.name}（{tool.description}）" for tool in hits)
        return ToolResult(
            content=f"找到 {len(hits)} 个可用工具：{listing}。已临时挂载，本轮可直接调用。",
            card_data={"query": args.query, "mounted": names},
        )


__all__ = [
    "DEFAULT_RESIDENT_TOOLS",
    "SEARCH_RESULT_LIMIT",
    "ToolSearchTool",
    "build_search_index",
    "configure_lazy_loading",
    "search_deferred_tools",
]
