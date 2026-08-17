"""口语评分路由测试：LLM mock（FakeLLM.chat_json），离线运行。"""

from __future__ import annotations

import httpx
import pytest

from app import deps
from app.main import create_app
from app.services.llm_client import LLMError
from tests.conftest import FakeLLM, auth_headers


@pytest.fixture
def app_with_llm(fake_llm: FakeLLM):
    app = create_app()
    app.dependency_overrides[deps.get_llm] = lambda: fake_llm
    return app


async def _evaluate(app, payload: dict, headers: dict | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/voice/evaluate", json=payload, headers=headers or auth_headers()
        )


async def test_evaluate_success(app_with_llm, fake_llm: FakeLLM):
    fake_llm.json_result = {
        "pronunciation": 82,
        "grammar": 75.5,
        "vocabulary": 80,
        "fluency": 70,
        "overall": 77,
        "feedback": "发音清晰，注意时态一致性。",
    }
    resp = await _evaluate(
        app_with_llm, {"scenario": "ielts_p1", "transcript": "I live in Beijing."}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pronunciation"] == 82.0
    assert body["grammar"] == 75.5
    assert body["overall"] == 77.0
    assert body["scenario"] == "ielts_p1"
    assert "时态" in body["feedback"]
    # LLM 被调用一次，prompt 含场景
    assert len(fake_llm.json_calls) == 1
    assert "ielts_p1" in fake_llm.json_calls[0]["system"]


async def test_evaluate_fallback_on_llm_error(app_with_llm, fake_llm: FakeLLM):
    fake_llm.json_result = LLMError("网关超时")
    resp = await _evaluate(app_with_llm, {"scenario": "daily", "transcript": "hi"})
    assert resp.status_code == 200  # 兜底不 5xx
    body = resp.json()
    assert body["overall"] == 0.0
    assert body["feedback"]


async def test_evaluate_fallback_on_bad_json(app_with_llm, fake_llm: FakeLLM):
    fake_llm.json_result = ["not", "a", "dict"]
    resp = await _evaluate(app_with_llm, {"scenario": "daily", "transcript": "hi"})
    assert resp.status_code == 200
    assert resp.json()["pronunciation"] == 0.0


async def test_evaluate_requires_internal_token(app_with_llm):
    resp = await _evaluate(
        app_with_llm,
        {"scenario": "daily", "transcript": "hi"},
        headers=auth_headers(token="wrong-token"),
    )
    assert resp.status_code == 401


async def test_evaluate_validation(app_with_llm):
    resp = await _evaluate(app_with_llm, {"scenario": ""})
    assert resp.status_code == 422
