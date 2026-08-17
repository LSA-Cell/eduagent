"""工具延迟加载与搜索发现测试（§4.3）：角色过滤 / 当轮挂载下轮卸载 / 常驻 ≤4 约束。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tools.base import ToolError
from app.tools.registry import EduToolRegistry
from app.tools.search import (
    ToolSearchTool,
    build_search_index,
    configure_lazy_loading,
    search_deferred_tools,
)
from tests.orchestration_fakes import (
    FakeDeps,
    ScriptedGateway,
    make_registry,
    make_tool,
    tool_call,
)


def _registry_with_deferred() -> EduToolRegistry:
    """常驻 2 个 + deferred 3 个（其一为教师专属）+ tool_search。"""
    registry = make_registry(
        make_tool("ocr_extract", description="识别图片文字"),
        make_tool("calculator", description="数学计算"),
        make_tool("web_search", description="联网检索权威教育资料", deferred=True),
        make_tool("flashcard_generator", description="生成记忆卡片", deferred=True),
        make_tool("exam_paper_generator", description="教师组卷", deferred=True,
                  role_required="teacher"),
        ToolSearchTool(),
    )
    registry.set_resident_tools("student", ["ocr_extract", "calculator"])
    registry.set_resident_tools("teacher", ["ocr_extract", "calculator"])
    return registry


class TestSearchIndex:
    def test_student_index_excludes_teacher_tools(self) -> None:
        """红线：角色不可见工具不进搜索索引（学生搜不到教师工具）。"""
        registry = _registry_with_deferred()
        student_index = {t.name for t in build_search_index(registry, "student")}
        assert "exam_paper_generator" not in student_index
        assert {"web_search", "flashcard_generator"} <= student_index
        # 教师可见
        teacher_index = {t.name for t in build_search_index(registry, "teacher")}
        assert "exam_paper_generator" in teacher_index

    def test_search_results_role_filtered(self) -> None:
        registry = _registry_with_deferred()
        hits = search_deferred_tools(registry, "student", "组卷")
        assert hits == []  # 学生搜“组卷”无任何结果（而非越权返回教师工具）
        hits = search_deferred_tools(registry, "teacher", "组卷")
        assert [t.name for t in hits] == ["exam_paper_generator"]

    def test_keyword_match_and_determinism(self) -> None:
        registry = _registry_with_deferred()
        hits = search_deferred_tools(registry, "student", "卡片")
        assert [t.name for t in hits] == ["flashcard_generator"]
        again = search_deferred_tools(registry, "student", "检索 资料")
        assert [t.name for t in again] == ["web_search"]


class TestResidentConstraint:
    def test_resident_over_4_rejected(self) -> None:
        """常驻工具集 >4 → 注册配置被拒绝（启动期校验）。"""
        registry = make_registry(*[make_tool(f"tool_{i}") for i in range(5)])
        with pytest.raises(ToolError):
            registry.set_resident_tools("student", [f"tool_{i}" for i in range(5)])

    def test_resident_unregistered_rejected(self) -> None:
        registry = make_registry(make_tool("tool_0"))
        with pytest.raises(ToolError):
            registry.set_resident_tools("student", ["tool_0", "ghost_tool"])

    def test_prompt_schemas_resident_plus_search_meta(self) -> None:
        """prompt schemas = 常驻 + tool_search；deferred 工具不进初始 prompt。"""
        registry = _registry_with_deferred()
        schemas = registry.prompt_schemas(role="student")
        names = {s["function"]["name"] for s in schemas}
        assert names == {"ocr_extract", "calculator", "tool_search"}
        # 挂载 deferred 后出现在当轮 schemas
        schemas = registry.prompt_schemas(role="student", mounted=("web_search",))
        names = {s["function"]["name"] for s in schemas}
        assert "web_search" in names
        # 角色不可见工具禁止挂载
        schemas = registry.prompt_schemas(role="student", mounted=("exam_paper_generator",))
        assert "exam_paper_generator" not in {s["function"]["name"] for s in schemas}

    def test_unconfigured_registry_keeps_legacy_behavior(self) -> None:
        registry = make_registry(make_tool("a"), make_tool("b", deferred=True))
        names = {s["function"]["name"] for s in registry.prompt_schemas(role="student")}
        assert names == {"a"}  # 未配置常驻清单：旧行为（全部非 deferred）


class TestLoopMounting:
    async def test_mount_current_turn_unmount_next(self) -> None:
        """命中当轮挂载（下个模型请求携带 Schema），下轮开始前自动卸载。"""
        from app.loop.agent_loop import edu_agent_loop
        from app.loop.deps_protocol import GatewayEvent

        registry = _registry_with_deferred()
        gateway = ScriptedGateway(stream_scripts=[
            # turn1：模型调用 tool_search 检索“卡片”
            [GatewayEvent(tool_calls=[tool_call("tool_search", {"query": "卡片"}, "c1")],
                          finish_reason="tool_calls")],
            # turn2：模型调用刚挂载的 flashcard_generator
            [GatewayEvent(tool_calls=[tool_call("flashcard_generator", {"content": "x"}, "c2")],
                          finish_reason="tool_calls")],
            # turn3：纯文本收尾
            [GatewayEvent(text="已完成", finish_reason="stop")],
        ])
        deps = FakeDeps(gateway=gateway, registry=registry)
        req = SimpleNamespace(user_id="u1", message="帮我做复习卡", session_id=None,
                              subject="math", model=None)

        events = [e async for e in edu_agent_loop(req, deps)]
        assert events[-1].event == "run.end"
        assert events[-1].data["finish_reason"] == "stop"

        assert len(gateway.stream_calls) == 3
        names_of = lambda i: {s["function"]["name"] for s in gateway.stream_calls[i]["tools"]}
        # turn1：仅常驻 + tool_search（deferred 不在初始 prompt）
        assert names_of(0) == {"ocr_extract", "calculator", "tool_search"}
        # turn2：当轮动态挂载 flashcard_generator（其余 deferred 仍不出现）
        assert "flashcard_generator" in names_of(1)
        assert "web_search" not in names_of(1)
        # turn3：挂载已自动卸载
        assert "flashcard_generator" not in names_of(2)

    async def test_configure_lazy_loading_marks_deferred(self) -> None:
        """configure_lazy_loading：非常驻工具全部 deferred，常驻清单按角色注册。"""
        registry = make_registry(
            make_tool("ocr_extract"), make_tool("calculator"),
            make_tool("ask_user_question"), make_tool("sub_agent_dispatch"),
            make_tool("web_search"), ToolSearchTool(),
        )
        configure_lazy_loading(registry, {
            "student": ["ocr_extract", "calculator", "ask_user_question", "sub_agent_dispatch"],
        })
        assert registry.get("web_search").deferred is True
        assert registry.get("calculator").deferred is False
        assert registry.get("tool_search").deferred is False
        names = {s["function"]["name"] for s in registry.prompt_schemas(role="student")}
        assert names == {"ocr_extract", "calculator", "ask_user_question",
                         "sub_agent_dispatch", "tool_search"}
