"""内置 EduTool 集（设计文档 §3.2 首批 8 个）。

calculator / equation_solver / web_search / knowledge_graph_query 为旧实现的
EduTool 适配（旧代码不动）；mistake_recorder / flashcard_generator /
ocr_extract / sub_agent_dispatch 为本包新实现。
教师 4 工具（ppt_generator 等）刻意不注册为独立工具——只能经
sub_agent_dispatch 触达对应子 Agent。
"""

from __future__ import annotations

from app.tools.registry import EduToolRegistry


def create_builtin_registry() -> EduToolRegistry:
    """构造含全部内置工具的注册表（注册即启动期元数据校验）。"""
    from app.tools.builtin.flashcard_generator import FlashcardGeneratorTool
    from app.tools.builtin.legacy_adapters import build_legacy_tools
    from app.tools.builtin.mistake_recorder import MistakeRecorderTool
    from app.tools.builtin.ocr_extract import OcrExtractTool
    from app.tools.builtin.sub_agent_dispatch import SubAgentDispatchTool

    registry = EduToolRegistry()
    registry.register_all(
        [
            *build_legacy_tools(),
            MistakeRecorderTool(),
            FlashcardGeneratorTool(),
            OcrExtractTool(),
            SubAgentDispatchTool(),
        ]
    )
    return registry


__all__ = ["create_builtin_registry"]
