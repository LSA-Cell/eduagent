"""内置工具集：导入本包即完成注册（各模块在 import 时向 registry 注册）。

新增工具时在此追加一行 import 即可。
"""

from __future__ import annotations

from app.tools import calculator, equation_solver, knowledge_graph, web_search  # noqa: F401
from app.tools.base import ToolContext, ToolRegistry, registry

__all__ = [
    "registry",
    "ToolContext",
    "ToolRegistry",
    "calculator",
    "equation_solver",
    "knowledge_graph",
    "web_search",
]
