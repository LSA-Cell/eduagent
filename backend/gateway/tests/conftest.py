"""Gateway 测试 fixtures：httpx MockTransport 模拟上游（vLLM / proxy），fakeredis 模拟 Redis。"""

from __future__ import annotations

import fakeredis.aioredis
import httpx
import pytest

from app.health import HealthRegistry
from app.main import create_app

VLLM_GENERAL = "http://vllm-general:8100"
VLLM_MATH = "http://vllm-math:8101"
PROXY_HOST = "proxy"

COMPLETION_GENERAL = {
    "id": "chatcmpl-internal",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "edu-general-14b",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "来自自有模型"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 3, "completion_tokens": 6, "total_tokens": 9},
}

COMPLETION_EXTERNAL = {
    "id": "chatcmpl-external",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "来自外部 API"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 3, "completion_tokens": 6, "total_tokens": 9},
}

SSE_INTERNAL = (
    'data: {"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"}}]}\n\n'
    'data: {"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"你"}}]}\n\n'
    'data: {"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"好"}}]}\n\n'
    "data: [DONE]\n\n"
).encode()

SSE_EXTERNAL = (
    'data: {"id":"e1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"外部"}}]}\n\n'
    "data: [DONE]\n\n"
).encode()


class StaticByteStream(httpx.AsyncByteStream):
    """一次性字节流：模拟真实上游的流式响应（httpx.Response(content=...) 构造的
    响应是"已消费"状态，无法再 aiter_raw，流式用例必须用 stream= 传入）。"""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self):
        yield self._data


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def app(fake_redis):
    application = create_app()
    application.state.redis = fake_redis
    application.state.health_registry = HealthRegistry(
        fake_redis, ttl_seconds=30, failure_threshold=3
    )
    # 测试中把首 token 超时收紧到 50ms，避免慢测试
    application.state.first_token_timeout = 0.05
    application.state.model_cards = [{"id": "edu-general-14b"}]
    return application


@pytest.fixture
async def make_client(app):
    """工厂：按上游 handler 构造下游测试客户端（MockTransport 注入为网关的 http_client）。"""
    clients: list[httpx.AsyncClient] = []

    def factory(handler) -> httpx.AsyncClient:
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        app.state.http_client = upstream
        downstream = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Internal-Token": "test-internal-token"},
        )
        clients.extend([upstream, downstream])
        return downstream

    yield factory
    for c in clients:
        await c.aclose()
