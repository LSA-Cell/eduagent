"""EduTool 注册表：启动期元数据校验、按角色过滤、OpenAI Schema 导出。

红线：缺 name/description/input_model 任一即拒绝注册（抛 ToolError）；
deferred 工具不进初始 prompt（openai_schemas 默认排除）。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from pydantic import BaseModel

from app.tools.base import EduTool, ToolError

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class EduToolRegistry:
    """新版工具注册表（与旧 ToolRegistry 并存，互不干扰）。"""

    def __init__(self) -> None:
        self._tools: dict[str, EduTool] = {}

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

    def openai_schemas(
        self, role: Optional[str] = None, *, include_deferred: bool = False
    ) -> list[dict[str, Any]]:
        """导出 OpenAI tools 数组；deferred 工具默认不进初始 prompt。"""
        tools = self.visible_for(role) if role else list(self._tools.values())
        schemas: list[dict[str, Any]] = []
        for tool in tools:
            if getattr(tool, "deferred", False) and not include_deferred:
                continue
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_model.model_json_schema(),
                    },
                }
            )
        return schemas
