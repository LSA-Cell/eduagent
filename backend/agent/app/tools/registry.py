"""EduTool 注册表：启动期元数据校验、按角色过滤、OpenAI Schema 导出。

红线：缺 name/description/input_model 任一即拒绝注册（抛 ToolError）；
deferred 工具不进初始 prompt（openai_schemas 默认排除）。

Wave D1（§4.3 工具延迟加载）：
- 常驻工具集按角色配置且 ≤4 个（set_resident_tools 超限即拒绝注册配置）；
- prompt_schemas(role, mounted=...)：常驻 + tool_search（元工具，不计入
  常驻配额）+ 当轮动态挂载（mounted 仅当轮有效，由 Loop/TurnState 管理
  自动卸载）；未配置常驻清单的注册表保持旧行为（全部非 deferred 可见）。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional, Sequence

from pydantic import BaseModel

from app.tools.base import EduTool, ToolError

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

MAX_RESIDENT_TOOLS = 4  # 常驻工具集上限（§4.3 红线：>4 需架构评审批准）
TOOL_SEARCH_NAME = "tool_search"  # 检索元工具：常驻但不计入常驻配额


class EduToolRegistry:
    """新版工具注册表（与旧 ToolRegistry 并存，互不干扰）。"""

    def __init__(self) -> None:
        self._tools: dict[str, EduTool] = {}
        self._residents: dict[str, list[str]] = {}  # role → 常驻工具清单（≤4）

    def register(self, tool: EduTool) -> EduTool:
        """注册工具；元数据不完整（缺 name/description/input_model）即拒绝。"""
        name = getattr(tool, "name", None)
        if not name or not isinstance(name, str) or not _NAME_RE.match(name):
            raise ToolError(f"工具 name 缺失或非法（需 snake_case）: {name!r}")
        if name in self._tools:
            raise ToolError(f"工具重复注册: {name}")
        description = getattr(tool, "description", None)
        if not description or not isinstance(description, str):
            raise ToolError(f"工具 {name} 缺少 description")
        input_model = getattr(tool, "input_model", None)
        if not (isinstance(input_model, type) and issubclass(input_model, BaseModel)):
            raise ToolError(f"工具 {name} 缺少合法的 input_model（须为 pydantic BaseModel 子类）")
        if not callable(getattr(tool, "call", None)):
            raise ToolError(f"工具 {name} 缺少 call 方法")
        self._tools[name] = tool
        return tool

    def register_all(self, tools: Iterable[EduTool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> EduTool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolError(f"未注册的工具: {name}") from None

    def maybe_get(self, name: str) -> Optional[EduTool]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def visible_for(self, role: str) -> list[EduTool]:
        """该角色可见的工具（role_required 为 None 或与角色匹配）。"""
        return [
            t
            for t in self._tools.values()
            if not getattr(t, "role_required", None) or t.role_required == role
        ]

    # ---- Wave D1：常驻清单与动态挂载（§4.3） --------------------------------
    def set_resident_tools(self, role: str, names: Sequence[str]) -> None:
        """配置某角色的常驻工具集；>4 或含未注册工具即拒绝（启动期校验）。"""
        if len(names) > MAX_RESIDENT_TOOLS:
            raise ToolError(
                f"常驻工具集 >{MAX_RESIDENT_TOOLS}（{role}: {len(names)} 个）"
                "需架构评审批准，注册配置被拒绝"
            )
        for name in names:
            if name not in self._tools:
                raise ToolError(f"常驻工具未注册: {name}")
        self._residents[role] = list(names)

    def resident_for(self, role: Optional[str]) -> Optional[list[str]]:
        """该角色的常驻清单；未配置返回 None（调用方保持旧行为）。"""
        if role and role in self._residents:
            return list(self._residents[role])
        return list(self._residents["*"]) if "*" in self._residents else None

    def prompt_schemas(
        self,
        role: Optional[str] = None,
        *,
        mounted: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        """进 System Prompt 的工具 Schema：常驻 + tool_search + 当轮挂载。

        未配置常驻清单时回退旧行为（openai_schemas(role)）；
        角色不可见工具无论常驻还是挂载都不导出（红线：越权等价护栏失效）。
        """
        residents = self.resident_for(role)
        if residents is None:
            base = self.openai_schemas(role=role)
            base_names = {s["function"]["name"] for s in base}
        else:
            visible = {t.name for t in self.visible_for(role)} if role else set(self._tools)
            names = [n for n in residents if n in visible]
            # tool_search 元工具常驻（不计入 ≤4 配额），模型据此发现 deferred 工具
            if TOOL_SEARCH_NAME in visible and TOOL_SEARCH_NAME not in names:
                names.append(TOOL_SEARCH_NAME)
            base = [self._openai_schema(self._tools[n]) for n in names]
            base_names = set(names)
        for name in mounted:
            if name in base_names:
                continue
            tool = self._tools.get(name)
            if tool is None:
                continue
            if role and tool not in self.visible_for(role):
                continue  # 角色不可见工具禁止挂载
            base.append(self._openai_schema(tool))
            base_names.add(name)
        return base

    @staticmethod
    def _openai_schema(tool: EduTool) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_model.model_json_schema(),
            },
        }

    def openai_schemas(
        self, role: Optional[str] = None, *, include_deferred: bool = False
    ) -> list[dict[str, Any]]:
        """导出 OpenAI tools 数组；deferred 工具默认不进初始 prompt。"""
        tools = self.visible_for(role) if role else list(self._tools.values())
        schemas: list[dict[str, Any]] = []
        for tool in tools:
            if getattr(tool, "deferred", False) and not include_deferred:
                continue
            schemas.append(self._openai_schema(tool))
        return schemas
