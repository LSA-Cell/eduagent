"""聊天链路边界测试：POST /v1/chat/stream（edu_agent_loop 驱动的唯一聊天端点）。

覆盖：事件序列与落库、X-User-Id 头优先、内部令牌鉴权、健康检查、掌握度矩阵。
全部离线：ASGITransport + FakeDeps（脚本化网关）+ FakeSession。
旧 /agent/chat（无护栏工具循环）已下线，回归测试见 test_old_route_removed。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from eduagent_shared.models import ChatSession, Message, MistakeRecord

from app import deps
from app.loop.deps_protocol import GatewayEvent
from app.main import create_app
from tests.conftest import FakeSession, auth_headers
from tests.orchestration_fakes import FakeDeps, ScriptedGateway


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    """解析 id:/event:/data: 三段式 SSE 帧 → [(event_type, data)]。"""
    events: list[tuple[str, dict[str, Any]]] = []
    for frame in body.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        event_type = "message"
        data_lines: list[str] = []
        for line in frame.splitlines():
            if line.startswith("event:"):
                event_type = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].lstrip())
        if data_lines:
            events.append((event_type, json.loads("\n".join(data_lines))))
    return events


@pytest.fixture
def app_and_deps(fake_session: FakeSession):
    """装配测试应用：编排 Deps 覆盖为 FakeDeps（脚本化网关 + 假会话）。"""
    gateway = ScriptedGateway(
        default_script=[
            GatewayEvent(text="你好"),
            GatewayEvent(text="，同学"),
            GatewayEvent(finish_reason="stop"),
        ]
    )
    fake_deps = FakeDeps(gateway=gateway, session=fake_session)
    app = create_app()
    app.dependency_overrides[deps.get_orchestration_deps] = lambda: fake_deps

    async def _fake_db():
        yield fake_session

    app.dependency_overrides[deps.get_db] = _fake_db
    return app, fake_session, gateway, fake_deps


async def _post_stream(
    app, payload: dict[str, Any], headers: dict[str, str] | None = None
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/v1/chat/stream", json=payload, headers=headers or auth_headers())


class TestChatStream:
    async def test_stream_events_and_persist(self, app_and_deps) -> None:
        app, session, _, fake_deps = app_and_deps

        resp = await _post_stream(
            app, {"user_id": "u-1", "message": "你好"},
            headers=auth_headers(user_id="u-1"),
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(resp.text)
        types = [t for t, _ in events]
        assert types == ["run.start", "message.delta", "message.delta", "run.end"]
        text = "".join(d.get("text", "") for t, d in events if t == "message.delta")
        assert text == "你好，同学"
        assert events[-1][1]["finish_reason"] == "stop"

        # 落库：1 个会话 + user/assistant 两条消息（Agent 统一落库，BFF 不写）
        sessions = [o for o in session.added if isinstance(o, ChatSession)]
        messages = [o for o in session.added if isinstance(o, Message)]
        assert len(sessions) == 1
        assert [m.role for m in messages] == ["user", "assistant"]
        assert messages[1].content == "你好，同学"

        # 完成流后派发错题提取 + 记忆提取两个异步任务
        tags = sorted(tag for tag, _ in fake_deps.celery_sent)
        assert tags == ["agent.memory.extract", "agent.mistake.extract"]
        kw = dict(fake_deps.celery_sent)["agent.mistake.extract"]
        assert kw["user_id"] == "u-1"
        assert kw["session_id"] == str(sessions[0].id)

    async def test_user_id_from_header(self, app_and_deps) -> None:
        """BFF 约定：X-User-Id 头优先于 body。"""
        app, _, _, fake_deps = app_and_deps
        resp = await _post_stream(
            app,
            {"user_id": "body-user", "message": "hi"},
            headers=auth_headers(user_id="header-user"),
        )
        assert resp.status_code == 200
        assert dict(fake_deps.celery_sent)["agent.mistake.extract"]["user_id"] == "header-user"

    async def test_missing_user_id_400(self, app_and_deps) -> None:
        app, *_ = app_and_deps
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat/stream",
                json={"message": "hi"},
                headers={"X-Internal-Token": "test-internal-token"},
            )
        assert resp.status_code in (400, 422)  # 缺 user_id：请求体验证拒绝

    async def test_old_route_removed(self, app_and_deps) -> None:
        """旧 /agent/chat 已下线：不再暴露无护栏的工具循环入口。"""
        app, *_ = app_and_deps
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/agent/chat", json={"user_id": "u", "message": "hi"},
                headers=auth_headers(),
            )
        assert resp.status_code in (404, 405)


class TestAuthAndHealth:
    async def test_missing_internal_token_401(self, app_and_deps) -> None:
        app, *_ = app_and_deps
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/chat/stream", json={"user_id": "u", "message": "hi"})
            assert resp.status_code == 401
            resp = await client.get("/analytics/knowledge-mastery",
                                    headers={"X-User-Id": "u"})
            assert resp.status_code == 401

    async def test_health_no_auth(self, app_and_deps) -> None:
        app, *_ = app_and_deps
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200 and resp.json() == {"status": "ok"}


class TestKnowledgeMastery:
    async def test_matrix(self, app_and_deps) -> None:
        app, session, _, _ = app_and_deps
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
