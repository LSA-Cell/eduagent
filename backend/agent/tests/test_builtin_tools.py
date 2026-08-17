"""内置 EduTool 测试（fake 依赖，全部离线）。

覆盖：4 个新工具（mistake_recorder / flashcard_generator / ocr_extract /
sub_agent_dispatch）的行为与元数据，以及旧工具适配层进新注册表。
"""

from __future__ import annotations

import json

import pytest

from eduagent_shared.models import Flashcard, MistakeRecord, Presentation

from app.loop.deps_protocol import GatewayEvent
from app.services.llm_client import ChatResult
from app.tools.base import ToolContext, ToolResult
from app.tools.builtin import create_builtin_registry
from app.tools.builtin.flashcard_generator import (
    FlashcardGeneratorArgs,
    FlashcardGeneratorTool,
)
from app.tools.builtin.mistake_recorder import MistakeRecorderArgs, MistakeRecorderTool
from app.tools.builtin.ocr_extract import OcrExtractArgs, OcrExtractTool
from app.tools.builtin.sub_agent_dispatch import SubAgentDispatchArgs, SubAgentDispatchTool
from tests.conftest import FakeSession
from tests.orchestration_fakes import FakeDeps, ScriptedGateway


class TestBuiltinRegistry:
    def test_eight_builtin_tools_registered(self) -> None:
        """首批 8 个内置工具全部注册（注册即通过启动期元数据校验）。"""
        registry = create_builtin_registry()
        assert registry.names() == [
            "calculator", "equation_solver", "flashcard_generator",
            "knowledge_graph_query", "mistake_recorder", "ocr_extract",
            "sub_agent_dispatch", "web_search",
        ]

    def test_teacher_tools_not_registered(self) -> None:
        """红线：教师 4 工具不实现为独立工具，只能经 sub_agent_dispatch 触达。"""
        registry = create_builtin_registry()
        for name in (
            "ppt_generator", "lesson_plan_generator",
            "exam_paper_generator", "learning_analysis",
        ):
            assert registry.maybe_get(name) is None

    async def test_legacy_adapter_calculator(self) -> None:
        """旧 calculator 经适配层在新注册表可用（旧实现未动）。"""
        registry = create_builtin_registry()
        tool = registry.get("calculator")
        assert tool.is_read_only and tool.is_concurrency_safe
        ctx = ToolContext(user_id="u1", deps=FakeDeps())
        result = await tool.call(tool.input_model(expression="3*(4+5)"), ctx)
        assert "27" in result.content


class TestMistakeRecorder:
    async def test_records_mistake_via_injected_session(self) -> None:
        session = FakeSession()
        deps = FakeDeps(session=session)
        ctx = ToolContext(user_id="u1", deps=deps, subject="math")
        tool = MistakeRecorderTool()

        result = await tool.call(
            MistakeRecorderArgs(
                question="2x+3=7 求解", user_answer="x=1",
                correct_answer="x=2", analysis="移项错误",
                knowledge_tags=["一元一次方程"],
            ),
            ctx,
        )

        records = [o for o in session.added if isinstance(o, MistakeRecord)]
        assert len(records) == 1
        record = records[0]
        assert record.user_id == "u1"
        assert record.subject == "math"  # 缺省取会话学科
        assert record.correct_answer == "x=2"
        assert session.commits == 1
        assert "已记录错题" in result.content
        assert result.card_type == "mistake_card"
        assert result.card_data["mistake_id"] == str(record.id)
        assert tool.is_read_only is False  # 写工具串行

    async def test_explicit_subject_wins(self) -> None:
        session = FakeSession()
        deps = FakeDeps(session=session)
        ctx = ToolContext(user_id="u1", deps=deps, subject="math")
        tool = MistakeRecorderTool()
        await tool.call(
            MistakeRecorderArgs(question="题", subject="physics"), ctx
        )
        record = [o for o in session.added if isinstance(o, MistakeRecord)][0]
        assert record.subject == "physics"


class TestFlashcardGenerator:
    async def test_generates_cards_via_gateway_and_session(self) -> None:
        payload = json.dumps(
            [{"front": "Q1", "back": "A1"}, {"front": "Q2", "back": "A2"}],
            ensure_ascii=False,
        )
        gateway = ScriptedGateway(chat_results=[ChatResult(content=payload)])
        session = FakeSession()
        deps = FakeDeps(gateway=gateway, session=session)
        ctx = ToolContext(user_id="u1", deps=deps, subject="english")
        tool = FlashcardGeneratorTool()

        result = await tool.call(
            FlashcardGeneratorArgs(content="abandon: 放弃；ability: 能力", count=2),
            ctx,
        )

        assert result.card_type == "flashcard_group"
        assert result.card_data["created"] == 2
        assert result.card_data["cards"] == [
            {"front": "Q1", "back": "A1"}, {"front": "Q2", "back": "A2"},
        ]
        cards = [o for o in session.added if isinstance(o, Flashcard)]
        assert len(cards) == 2
        assert all(c.deck == result.card_data["group_id"] for c in cards)
        assert all(c.user_id == "u1" and c.subject == "english" for c in cards)
        assert session.commits == 1

    async def test_llm_bad_output_raises(self) -> None:
        gateway = ScriptedGateway(chat_results=[ChatResult(content="不是JSON")])
        deps = FakeDeps(gateway=gateway, session=FakeSession())
        ctx = ToolContext(user_id="u1", deps=deps)
        tool = FlashcardGeneratorTool()
        with pytest.raises(Exception):
            await tool.call(FlashcardGeneratorArgs(content="内容"), ctx)


class _FakeMultimodal:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def ocr_extract(self, *, image_url=None, file_url=None, prompt=None):
        self.calls.append(
            {"image_url": image_url, "file_url": file_url, "prompt": prompt}
        )
        return {"text": "3+4=7", "latex_blocks": ["x^2+1"]}


class TestOcrExtract:
    async def test_ocr_via_injected_multimodal_client(self) -> None:
        client = _FakeMultimodal()
        deps = FakeDeps(multimodal=client)
        ctx = ToolContext(user_id="u1", deps=deps)
        tool = OcrExtractTool()

        result = await tool.call(
            OcrExtractArgs(image_url="https://minio.test/q.png"), ctx
        )

        assert client.calls[0]["image_url"] == "https://minio.test/q.png"
        assert "3+4=7" in result.content
        assert "x^2+1" in result.content
        assert tool.is_read_only and tool.is_concurrency_safe

    async def test_multimodal_missing_degrades(self) -> None:
        deps = FakeDeps()  # multimodal=None
        ctx = ToolContext(user_id="u1", deps=deps)
        tool = OcrExtractTool()
        result = await tool.call(OcrExtractArgs(image_url="x"), ctx)
        assert "未配置" in result.content

    def test_args_require_a_source(self) -> None:
        with pytest.raises(Exception):
            OcrExtractArgs()


class TestSubAgentDispatch:
    async def test_unknown_agent_returns_structured_message(self) -> None:
        deps = FakeDeps()
        ctx = ToolContext(user_id="u1", deps=deps)
        tool = SubAgentDispatchTool()
        result = await tool.call(SubAgentDispatchArgs(agent="ghost_agent"), ctx)
        assert "未注册的子 Agent" in result.content
        assert "grading_agent" in result.content  # 附可用清单

    async def test_teacher_only_agent_denied_for_student(self) -> None:
        """学生经 dispatch 触达 ppt_generator（教师专属）→ 拒绝。"""
        deps = FakeDeps(session=FakeSession())
        ctx = ToolContext(user_id="u1", deps=deps, role="student")
        tool = SubAgentDispatchTool()
        result = await tool.call(
            SubAgentDispatchArgs(agent="ppt_generator", params={"topic": "t"}), ctx
        )
        assert "仅教师" in result.content
        assert result.card_data["status"] == "forbidden"
        assert deps.celery_sent == []

    async def test_async_agent_dispatch_accepted_card(self) -> None:
        """教师派发 ppt_generator → 建行 + 投递既有 gen task + 受理卡片。"""
        session = FakeSession()
        deps = FakeDeps(session=session)
        ctx = ToolContext(user_id="t1", deps=deps, role="teacher", is_teacher=True)
        tool = SubAgentDispatchTool()

        result = await tool.call(
            SubAgentDispatchArgs(
                agent="ppt_generator", task="做一份课件",
                params={"topic": "二次函数", "grade": "初三", "subject": "math"},
            ),
            ctx,
        )

        assert result.card_type == "ppt_preview"
        assert result.card_data["status"] == "accepted"
        assert result.card_data["task_id"].startswith("sa-")
        records = [o for o in session.added if isinstance(o, Presentation)]
        assert len(records) == 1 and records[0].title == "二次函数"
        assert deps.celery_sent == [
            ("agent.gen.presentation", {"record_id": result.card_data["record_id"]})
        ]

    async def test_sync_agent_runs_in_process(self) -> None:
        """oral_scoring_agent（async_via=none）进程内同步执行，单 ToolResult 返回。"""
        gateway = ScriptedGateway(
            default_script=[GatewayEvent(text="评分 90 分", finish_reason="stop")]
        )
        deps = FakeDeps(gateway=gateway)
        ctx = ToolContext(user_id="u1", deps=deps, role="student")
        tool = SubAgentDispatchTool()

        result = await tool.call(
            SubAgentDispatchArgs(
                agent="oral_scoring_agent", task="评分",
                params={"transcript": "hello world"},
            ),
            ctx,
        )

        assert isinstance(result, ToolResult)
        assert "评分 90 分" in result.content
        assert result.card_data["agent"] == "oral_scoring_agent"

    async def test_depth_limit_refused(self) -> None:
        """红线：最大深度 2——子 Agent 上下文（depth=2）里再派发被拒绝。"""
        deps = FakeDeps()
        ctx = ToolContext(user_id="u1", deps=deps, depth=2)
        tool = SubAgentDispatchTool()
        result = await tool.call(
            SubAgentDispatchArgs(agent="oral_scoring_agent", task="x"), ctx
        )
        assert "深度" in result.content
        assert result.card_data["status"] == "depth_exceeded"
