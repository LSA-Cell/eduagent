"""旧工具（calculator/equation_solver/web_search/knowledge_graph）的 EduTool 适配层。

旧实现（app/tools/*.py，注册进旧 ToolRegistry）一行不动；此处仅包一层
EduTool 元数据 + Pydantic 入参，使它们能进新注册表（EduToolRegistry）参与
护栏链/分批并发/瘦身/审计。ctx.settings 缺省时从 ctx.deps 补（新 Loop 路径）。
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel, Field

from app.tools.base import EduToolBase, ToolContext, ToolResult


class CalculatorArgs(BaseModel):
    expression: str = Field(min_length=1, description="数学表达式，如 3*(4+5)/2")


class EquationSolverArgs(BaseModel):
    equation: str = Field(min_length=1, description="方程，如 2*x+3=7 或 x^2-5x+6=0")
    variable: str = "x"


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, description="检索关键词")
    num: int = Field(default=5, ge=1, le=10)


class KnowledgeGraphArgs(BaseModel):
    concept: str = Field(min_length=1, description="知识点名称，如 一元二次方程")


def _with_settings(ctx: ToolContext) -> ToolContext:
    """新 Loop 不填 ctx.settings：从 deps 生产实现补（fake deps 无 _settings 时原样）。"""
    if ctx.settings is not None or ctx.deps is None:
        return ctx
    settings = getattr(ctx.deps, "_settings", None)
    if settings is None:
        return ctx
    return dataclasses.replace(ctx, settings=settings)


class _LegacyAdapter(EduToolBase):
    """旧 handler（async (dict, ctx) -> dict）→ EduTool 的通用适配。"""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_model: type[BaseModel],
        handler: Callable[[dict[str, Any], ToolContext], Awaitable[dict[str, Any]]],
        timeout_s: float = 10,
    ) -> None:
        self.name = name
        self.description = description
        self.input_model = input_model
        self._handler = handler
        self.timeout_s = timeout_s
        self.is_read_only = True
        self.is_concurrency_safe = True

    async def call(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        """调用旧 handler；旧契约的 {"error": ...} 转为可读文本回注（不算执行异常）。"""
        out = await self._handler(args.model_dump(mode="python"), _with_settings(ctx))
        if isinstance(out, dict) and out.get("error"):
            return ToolResult(content=f"{self.description}未成功：{out['error']}")
        return ToolResult(content=json.dumps(out, ensure_ascii=False, default=str))


def build_legacy_tools() -> list[EduToolBase]:
    """构造 4 个旧工具的 EduTool 适配实例（import 旧模块触发其旧注册，无冲突）。"""
    from app.tools.calculator import CALCULATOR_SCHEMA, handle_calculator
    from app.tools.equation_solver import EQUATION_SOLVER_SCHEMA, handle_equation_solver
    from app.tools.knowledge_graph import KNOWLEDGE_GRAPH_SCHEMA, handle_knowledge_graph
    from app.tools.web_search import WEB_SEARCH_SCHEMA, handle_web_search

    return [
        _LegacyAdapter(
            name="calculator", description=str(CALCULATOR_SCHEMA["description"]),
            input_model=CalculatorArgs, handler=handle_calculator,
        ),
        _LegacyAdapter(
            name="equation_solver", description=str(EQUATION_SOLVER_SCHEMA["description"]),
            input_model=EquationSolverArgs, handler=handle_equation_solver,
        ),
        _LegacyAdapter(
            name="web_search", description=str(WEB_SEARCH_SCHEMA["description"]),
            input_model=WebSearchArgs, handler=handle_web_search, timeout_s=15,
        ),
        _LegacyAdapter(
            name="knowledge_graph_query", description=str(KNOWLEDGE_GRAPH_SCHEMA["description"]),
            input_model=KnowledgeGraphArgs, handler=handle_knowledge_graph,
        ),
    ]
