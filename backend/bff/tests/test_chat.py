"""BFF 聊天链路测试：/chat/completions 与 /chat/stream 统一转发 Agent 编排层。

红线验证：
- 请求打到 Agent（而非推理网关），携带 X-Internal-Token + X-User-Id；
- 9 类事件 → OpenAI chunk 翻译（旧客户端契约不变）；
- BFF 不写 messages（消除双落库，落库由 Agent 负责）；
- 会话归属校验 404、Agent 不可达 502。

全部离线：httpx.MockTransport 模拟 Agent 上游。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app import deps
from tests.conftest import auth_headers, register_and_login


def _sse(events: list[tuple[str, dict[str, Any]]]) -> bytes:
    frames = []
    for i, (event_type, data) in enumerate(events, start=1):
        payload = {"seq": i, **data}
        frames.append(f"id: {i}\nevent: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n")
    return "".join(frames).encode()


AGENT_EVENTS = _sse([
    ("run.start", {"run_id": "r1", "session_id": "s1"}),
    ("message.delta", {"text": "你好"}),
    ("message.delta", {"text": "，同学"}),
    ("tool.start", {"tool_name": "calculator"}),  # 富事件：旧客户端契约中跳过
    ("run.end", {"finish_reason": "stop", "usage": {"tokens_used": 10}}),
])


@pytest.fixture
async def agent_capture(app):
    """把 BFF 的出口 HTTP 客户端替换为 MockTransport，返回脚本化 Agent SSE。"""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode())

        async def event_body():  # MockTransport 下字节串响应会被提前消费，须用异步流
            yield AGENT_EVENTS

        return httpx.Response(
            200, content=event_body(),
            headers={"content-type": "text/event-stream"},
        )

    old_client = app.state.http_client
    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    yield captured
    await app.state.http_client.aclose()
    app.state.http_client = old_client


def _parse_openai_chunks(body: str) -> list[dict[str, Any]]:
    chunks = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            chunks.append({"done": True})
            continue
        chunks.append(json.loads(data))
    return chunks


class TestCompletions:
    async def test_stream_translated_to_openai_chunks(self, app, agent_capture, client) -> None:
        token = await register_and_login(client)
        resp = await client.post(
            "/api/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "你好"}], "stream": True},
            headers=auth_headers(token),
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        # 打到 Agent 编排层，而非推理网关
        assert agent_capture["url"].endswith("/v1/chat/stream")
        assert "gateway" not in agent_capture["url"]
        assert agent_capture["headers"].get("x-internal-token")
        assert agent_capture["headers"].get("x-user-id")
        # 身份字段由 BFF 依据用户记录注入
        body = agent_capture["body"]
        assert body["message"] == "你好"
        assert body["role"] == "student"
        assert body["quota_tier"] == "free"

        chunks = _parse_openai_chunks(resp.text)
        deltas = [c["choices"][0]["delta"] for c in chunks if "choices" in c]
        text = "".join(d.get("content", "") for d in deltas)
        assert text == "你好，同学"
        assert chunks[-1] == {"done": True}
        finish = [c["choices"][0]["finish_reason"] for c in chunks if "choices" in c]
        assert "stop" in finish

    async def test_nonstream_aggregated(self, app, agent_capture, client) -> None:
        token = await register_and_login(client, "13800000002")
        resp = await client.post(
            "/api/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "1+1"}], "stream": False},
            headers=auth_headers(token),
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["object"] == "chat.completion"
        assert payload["choices"][0]["message"]["content"] == "你好，同学"
        assert payload["choices"][0]["finish_reason"] == "stop"

    async def test_bff_does_not_persist_messages(self, app, agent_capture, client, chat_store) -> None:
        """红线：BFF 不写 messages（Agent 统一落库，消除双落库）。"""
        token = await register_and_login(client, "13800000003")
        session = await chat_store.create_session(user_id=uuid_of(client, token), title="t")
        resp = await client.post(
            "/api/v1/chat/completions",
            json={
                "session_id": str(session.id),
                "messages": [{"role": "user", "content": "你好"}],
            },
            headers=auth_headers(token),
        )
        assert resp.status_code == 200
        assert chat_store._messages == {}  # 无任何消息写入

    async def test_session_ownership_404(self, app, agent_capture, client, chat_store) -> None:
        import uuid as _uuid

        token = await register_and_login(client, "13800000004")
        resp = await client.post(
            "/api/v1/chat/completions",
            json={
                "session_id": str(_uuid.uuid4()),  # 不存在/他人会话
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers=auth_headers(token),
        )
        assert resp.status_code == 404
        assert "url" not in agent_capture  # 未触发上游调用

    async def test_agent_unreachable_502(self, app, client) -> None:
        def failing_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        old_client = app.state.http_client
        app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(failing_handler))
        try:
            token = await register_and_login(client, "13800000005")
            resp = await client.post(
                "/api/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}]},
                headers=auth_headers(token),
            )
            assert resp.status_code == 502
        finally:
            await app.state.http_client.aclose()
            app.state.http_client = old_client


class TestStreamPassthrough:
    async def test_raw_events_passthrough(self, app, agent_capture, client) -> None:
        token = await register_and_login(client, "13800000006")
        resp = await client.post(
            "/api/v1/chat/stream",
            json={"messages": [{"role": "user", "content": "你好"}]},
            headers=auth_headers(token),
        )
        assert resp.status_code == 200
        # 原样透传：事件帧未被翻译（含 tool.start 等富事件）
        assert "event: run.start" in resp.text
        assert "event: tool.start" in resp.text
        assert "event: run.end" in resp.text


def uuid_of(client, token):
    """取当前用户 id（测试辅助：直接解析 access token sub）。"""
    from eduagent_shared.security.jwt import decode_token
    import uuid

    claims = decode_token(token, expected_type="access")
    return uuid.UUID(str(claims["sub"]))
