"""聊天路由与应用装配测试：SSE 流式透传、tool 循环路径、鉴权、健康检查、掌握度矩阵。

全部离线：ASGITransport + 依赖覆盖（假会话 / 脚本化 LLM）+ Celery 任务 .delay 打桩。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from eduagent_shared.models import ChatSession, Message, MistakeRecord

from app import deps
from app.main import create_app
from app.services.llm_client import ChatResult, LLMError, ToolCall
from app.tasks import memory_tasks, mistake_tasks
from tests.conftest import FakeLLM, FakeSession, auth_headers


def _parse_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for frame in body.split("\n\n"):
        frame = frame.strip()
        if frame.startswith("data:"):
            events.append(json.loads(frame[5:].strip()))
    return events


@pytest.fixture
def app_and_session(fake_session: FakeSession, fake_llm: FakeLLM, monkeypatch):
    """装配测试应用：覆盖 DB/LLM/embedding 依赖，打桩 Celery 任务派发。"""
    app = create_app()

    async def _fake_db():
        yield fake_session

    app.dependency_overrides[deps.get_db] = _fake_db
    app.dependency_overrides[deps.get_llm] = lambda: fake_llm
    app.dependency_overrides[deps.get_embedding_provider] = lambda: None

    dispatched: list[tuple[str, dict[str, Any]]] = []

    def _fake_delay(tag: str):
        def _delay(**kwargs: Any):
            dispatched.append((tag, kwargs))
            return SimpleNamespace(id=f"task-{tag}-test")

        return _delay

    monkeypatch.setattr(mistake_tasks.extract_mistake, "delay", _fake_delay("mistake"))
    monkeypatch.setattr(memory_tasks.extract_memories, "delay", _fake_delay("memory"))
    return app, fake_session, fake_llm, dispatched


async def _post_chat(app, payload: dict[str, Any], headers: dict[str, str] | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/agent/chat", json=payload, headers=headers or auth_headers())


class TestChatStream:
    async def test_stream_passthrough_and_persist(self, app_and_session) -> None:
        app, session, llm, dispatched = app_and_session
        llm.stream_chunks = ["你好", "，同学"]  # 网关 SSE 增量

        resp = await _post_chat(
            app, {"user_id": "u-1", "message": "你好", "enable_tools": False},
            headers=auth_headers(user_id="u-1"),
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(resp.text)
        assert [e["type"] for e in events] == ["session", "delta", "delta", "done"]
        assert "".join(e.get("content", "") for e in events if e["type"] == "delta") == "你好，同学"
        assert events[0]["session_id"] == events[3]["session_id"]

        # 落库：1 个会话 + user/assistant 两条消息，路由层统一 commit
        sessions = [o for o in session.added if isinstance(o, ChatSession)]
        messages = [o for o in session.added if isinstance(o, Message)]
        assert len(sessions) == 1
        assert [m.role for m in messages] == ["user", "assistant"]
        assert messages[1].content == "你好，同学"
        assert session.commits == 1

        # 完成流后派发错题提取 + 记忆提取两个异步任务
        assert sorted(tag for tag, _ in dispatched) == ["memory", "mistake"]
        kw = dict(dispatched)["mistake"]
        assert kw["user_id"] == "u-1" and kw["session_id"] == events[0]["session_id"]

    async def test_tool_loop_path(self, app_and_session) -> None:
        app, session, llm, _ = app_and_session
        llm.chat_results = [
            ChatResult(tool_calls=[ToolCall(id="c1", name="calculator",
                                            arguments={"expression": "6*7"})]),
            ChatResult(content="6×7 = 42"),
        ]

        resp = await _post_chat(app, {"user_id": "u-1", "message": "6*7 等于几"})

        events = _parse_sse(resp.text)
        assert [e["type"] for e in events] == ["session", "delta", "done"]
        assert events[1]["content"] == "6×7 = 42"
        assert events[2]["tool_calls"] == ["calculator"]
        # 首轮请求携带工具 schema
        assert {t["function"]["name"] for t in llm.chat_calls[0]["tools"]} == {
            "calculator", "equation_solver", "knowledge_graph_query", "web_search"}
        # 工具轨迹写入 assistant 消息 metadata
        messages = [o for o in session.added if isinstance(o, Message)]
        assert messages[1].metadata_["tool_traces"][0]["name"] == "calculator"

    async def test_gateway_error_becomes_sse_error_frame(self, app_and_session) -> None:
        app, _, llm, _ = app_and_session
        llm.chat_results = [LLMError("网关超时")]

        resp = await _post_chat(app, {"user_id": "u-1", "message": "hi"})

        events = _parse_sse(resp.text)
        assert events[-1]["type"] == "error"
        assert "网关超时" in events[-1]["message"]

    async def test_user_id_from_header(self, app_and_session) -> None:
        """BFF 约定：X-User-Id 头优先于 body。"""
        app, session, llm, dispatched = app_and_session
        headers = auth_headers(user_id="header-user")
        resp = await _post_chat(
            app, {"user_id": "body-user", "message": "hi", "enable_tools": False}, headers
        )
        assert resp.status_code == 200
        assert dict(dispatched)["mistake"]["user_id"] == "header-user"


class TestAuthAndHealth:
    async def test_missing_internal_token_401(self, app_and_session) -> None:
        app, *_ = app_and_session
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/agent/chat", json={"user_id": "u", "message": "hi"})
            assert resp.status_code == 401
            resp = await client.get("/analytics/knowledge-mastery",
                                    headers={"X-User-Id": "u"})
            assert resp.status_code == 401

    async def test_health_no_auth(self, app_and_session) -> None:
        app, *_ = app_and_session
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200 and resp.json() == {"status": "ok"}


class TestKnowledgeMastery:
    async def test_matrix(self, app_and_session) -> None:
        app, session, _, _ = app_and_session
        session.add(MistakeRecord(user_id="u-1", question="q1", knowledge_points=["方程"],
                                  mastery=0.0, review_count=0))
        session.add(MistakeRecord(user_id="u-1", question="q2", knowledge_points=["方程"],
                                  mastery=0.8, review_count=2))
        session.add(MistakeRecord(user_id="u-1", question="q3", knowledge_points=["函数"],
                                  mastery=0.2, review_count=1))
        session.add(MistakeRecord(user_id="u-2", question="q4", knowledge_points=["几何"],
                                  mastery=0.9, review_count=5))  # 他人数据由 SQL 过滤
        await session.flush()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/analytics/knowledge-mastery", headers=auth_headers("u-1"))

        assert resp.status_code == 200
        matrix = resp.json()["matrix"]
        by_tag = {m["knowledge_tag"]: m for m in matrix}
        assert by_tag["方程"]["mistake_count"] == 2
        assert by_tag["方程"]["accuracy_proxy"] == 0.4
        assert 0 < by_tag["方程"]["mastery"] < 1
        assert by_tag["函数"]["level"] in {"weak", "critical", "familiar"}
        # 按掌握度升序（最薄弱在前）
        assert matrix == sorted(matrix, key=lambda m: m["mastery"])
