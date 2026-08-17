"""ToolUseSummary 工具摘要测试（§3.2）：模板命中率 / 小模型回退 / 隐私红线。"""

from __future__ import annotations

import asyncio

import pytest

from app.services.llm_client import ChatResult
from app.tools.summary import MAX_SUMMARY_CHARS, SummaryStats, summarize_tool_result


class TestTemplateHitRate:
    async def test_builtin_tools_all_template_hit(self) -> None:
        """遍历全部内置工具构造典型结果：模板命中率须 ≥80%（规格硬性指标）。"""
        from app.tools.builtin import create_builtin_registry
        from app.ask.tool import AskUserQuestionTool
        from app.plan.tool import SubmitPlanOutlineTool
        from app.todo.tool import TodoWriteTool

        registry = create_builtin_registry()
        registry.register_all([AskUserQuestionTool(), TodoWriteTool(), SubmitPlanOutlineTool()])
        typical_args = {
            "calculator": {"expression": "3*(4+5)"},
            "equation_solver": {"equation": "2*x+3=7"},
            "web_search": {"query": "一元二次方程 求根公式"},
            "knowledge_graph_query": {"concept": "二次函数"},
            "mistake_recorder": {"question": "2x+3=7", "user_answer": "x=1"},
            "flashcard_generator": {"content": "课文", "count": 10},
            "ocr_extract": {"image_url": "https://minio.test/x.png"},
            "sub_agent_dispatch": {"agent": "grading_agent", "task": "批改"},
            "ask_user_question": {"questions": [{"question": "q", "options": []}]},
            "todo_write": {"items": [{"content": "c", "status": "pending"}]},
            "submit_plan_outline": {"outline": {}},
        }
        stats = SummaryStats()
        for name in registry.names():
            summary = await summarize_tool_result(
                name, typical_args.get(name, {}), "执行完成", stats=stats
            )
            assert summary and len(summary) <= MAX_SUMMARY_CHARS
        assert stats.total == len(registry.names())
        assert stats.template_hit_rate >= 0.8, f"模板命中率 {stats.template_hit_rate:.0%} <80%"


class _SlowGateway:
    """小模型路径超时替身（200ms > 100ms 上限）。"""

    async def chat(self, messages, *, model=None, tools=None, max_tokens=None) -> ChatResult:
        await asyncio.sleep(0.2)
        return ChatResult(content="不应等到我")


class _EchoGateway:
    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict] = []

    async def chat(self, messages, *, model=None, tools=None, max_tokens=None) -> ChatResult:
        self.calls.append({"messages": messages})
        return ChatResult(content=self._content)


class TestLlmFallback:
    async def test_timeout_falls_back_to_template(self) -> None:
        """小模型超时（>100ms）必须回退通用模板，且不拖慢调用方。"""
        stats = SummaryStats()
        summary = await summarize_tool_result(
            "unknown_tool", {"q": 1}, "执行完成",
            gateway=_SlowGateway(), stats=stats,
        )
        assert summary == "使用工具 unknown_tool：完成"
        assert stats.llm_attempts == 1 and stats.llm_failures == 1

    async def test_llm_success_used_and_capped(self) -> None:
        gateway = _EchoGateway("提炼了" * 30)  # 超 50 字
        stats = SummaryStats()
        summary = await summarize_tool_result(
            "custom_tool", {}, "完成", gateway=gateway, stats=stats
        )
        assert len(summary) <= MAX_SUMMARY_CHARS
        assert stats.llm_attempts == 1 and stats.llm_failures == 0


class TestPrivacyRedline:
    async def test_summary_excludes_user_plaintext(self) -> None:
        """摘要禁止包含用户隐私原文（模板路径与 LLM 路径都不含结果原文）。"""
        secret = "我的作文原文：那是一个风雪交加的夜晚，我独自走在回家的路上" * 5
        # 模板路径（mistake_recorder 有模板）
        summary = await summarize_tool_result(
            "mistake_recorder", {"question": secret}, f"已记录错题。{secret}"
        )
        assert secret[:20] not in summary
        # LLM 路径：未覆盖工具的结果原文同样禁止进摘要（且不进小模型 prompt）
        gateway = _EchoGateway("已生成摘要")
        summary = await summarize_tool_result(
            "essay_polisher", {"text": secret}, secret, gateway=gateway
        )
        assert secret[:20] not in summary
        prompt_text = str(gateway.calls[0]["messages"])
        assert secret[:20] not in prompt_text  # 原文根本没喂给小模型


class TestExecutorIntegration:
    async def test_slim_placeholder_uses_summary(self) -> None:
        """执行器瘦身占位改走 ≤50 字摘要（不再保留原文 500 字前缀）。"""
        from app.loop.turn_config import TurnConfig
        from app.tools.base import ToolContext
        from app.tools.executor import ToolCallRequest, ToolExecutor
        from tests.orchestration_fakes import (
            FakeDeps, ScriptedGateway, make_registry, make_tool,
        )

        long_result = "作" * 5000  # 超 max_result_chars 触发瘦身
        registry = make_registry(make_tool("echo_tool", result=long_result, max_result_chars=100))
        deps = FakeDeps(gateway=ScriptedGateway(), registry=registry)
        executor = ToolExecutor(registry, deps.guardrails, deps)
        config = TurnConfig(user_id="u1")
        ctx = ToolContext(user_id="u1", deps=deps, run_id="r1")

        [execution] = await executor.execute(
            [ToolCallRequest(id="c1", name="echo_tool", arguments={"text": "hi"})],
            config, ctx,
        )
        assert execution.ok
        assert "结果已瘦身" in execution.content
        first_line = execution.content.splitlines()[0]
        assert len(first_line) <= MAX_SUMMARY_CHARS
        assert "作" * 100 not in execution.content  # 原文前缀不再保留
        assert execution.summary and len(execution.summary) <= MAX_SUMMARY_CHARS
