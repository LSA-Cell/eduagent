"""EduToolRegistry 测试：启动期元数据校验、deferred 过滤、角色可见性。"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.tools.base import EduToolBase, ToolError
from app.tools.registry import EduToolRegistry

from tests.orchestration_fakes import make_tool


class TestMetadataValidation:
    def test_missing_name_rejected(self) -> None:
        tool = make_tool("x")
        tool.name = ""  # type: ignore[misc]
        with pytest.raises(ToolError):
            EduToolRegistry().register(tool)

    def test_missing_description_rejected(self) -> None:
        tool = make_tool("x")
        tool.description = ""  # type: ignore[misc]
        with pytest.raises(ToolError):
            EduToolRegistry().register(tool)

    def test_missing_input_model_rejected(self) -> None:
        tool = make_tool("x")
        tool.input_model = dict  # type: ignore[misc]
        with pytest.raises(ToolError):
            EduToolRegistry().register(tool)

    def test_duplicate_rejected(self) -> None:
        registry = EduToolRegistry()
        registry.register(make_tool("dup"))
        with pytest.raises(ToolError):
            registry.register(make_tool("dup"))

    def test_non_snake_case_name_rejected(self) -> None:
        with pytest.raises(ToolError):
            EduToolRegistry().register(make_tool("Bad-Name"))


class TestSchemaExport:
    def test_deferred_excluded_from_initial_prompt(self) -> None:
        registry = EduToolRegistry()
        registry.register(make_tool("calculator"))
        registry.register(make_tool("ocr_extract", deferred=True))
        schemas = registry.openai_schemas()
        assert [s["function"]["name"] for s in schemas] == ["calculator"]
        # 工具检索场景可显式带上 deferred
        all_schemas = registry.openai_schemas(include_deferred=True)
        assert sorted(s["function"]["name"] for s in all_schemas) == ["calculator", "ocr_extract"]

    def test_schema_carries_input_model(self) -> None:
        class Args(BaseModel):
            expression: str

        registry = EduToolRegistry()
        registry.register(make_tool("calculator", input_model=Args))
        [schema] = registry.openai_schemas()
        assert schema["type"] == "function"
        props = schema["function"]["parameters"]["properties"]
        assert "expression" in props


class TestRoleVisibility:
    def test_teacher_tool_hidden_from_student(self) -> None:
        registry = EduToolRegistry()
        registry.register(make_tool("calculator"))
        registry.register(make_tool("lesson_plan_generator", role_required="teacher"))
        assert registry.names() == ["calculator", "lesson_plan_generator"]
        assert [t.name for t in registry.visible_for("student")] == ["calculator"]
        assert len(registry.visible_for("teacher")) == 2
        student_schemas = registry.openai_schemas(role="student")
        assert [s["function"]["name"] for s in student_schemas] == ["calculator"]


class TestLegacyCoexistence:
    def test_old_registry_untouched(self) -> None:
        """旧 ToolRegistry / @registry.tool 全局对象继续可用。"""
        from app.tools.base import ToolRegistry, registry

        assert isinstance(registry, ToolRegistry)
        local = ToolRegistry()

        @local.tool({"name": "legacy", "description": "旧工具", "parameters": {}})
        async def handler(arguments, ctx):
            return {"ok": True}

        assert local.get("legacy").openai_schema()["function"]["name"] == "legacy"
