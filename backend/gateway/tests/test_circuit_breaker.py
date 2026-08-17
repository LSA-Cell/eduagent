"""熔断：连续失败标记 unhealthy，熔断期间不再请求实例；成功后清除标记。"""

from __future__ import annotations

import httpx

from tests.conftest import COMPLETION_EXTERNAL, COMPLETION_GENERAL


async def test_circuit_breaker_marks_unhealthy_and_skips(make_client, fake_redis):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "vllm-general":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json=COMPLETION_EXTERNAL)

    payload = {
        "model": "edu-general-14b",
        "messages": [{"role": "user", "content": "hi"}],
        "user_id": "u-1",
    }
    async with make_client(handler) as client:
        # 连续 3 次失败（阈值），每次都降级成功
        for _ in range(3):
            resp = await client.post("/v1/chat/completions", json=payload)
            assert resp.status_code == 200
        # 实例已被熔断
        assert await fake_redis.exists("gw:unhealthy:http://vllm-general:8100")
        vllm_calls = calls.count("vllm-general")
        assert vllm_calls == 3

        # 第 4 次：熔断窗口内跳过实例，直接走 external
        resp = await client.post("/v1/chat/completions", json=payload)
        assert resp.status_code == 200
        assert calls.count("vllm-general") == vllm_calls


async def test_success_clears_failure_state(make_client, fake_redis):
    state = {"fail": True}
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if state["fail"]:
            state["fail"] = False
            raise httpx.ConnectError("flaky", request=request)
        return httpx.Response(200, json=COMPLETION_GENERAL)

    async with make_client(handler) as client:
        # 第一次失败（auto_failover=false → 502），失败计数 +1
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "edu-general-14b",
                "messages": [{"role": "user", "content": "hi"}],
                "auto_failover": False,
            },
        )
        assert resp.status_code == 502
        assert await fake_redis.exists("gw:fail:http://vllm-general:8100")

        # 第二次成功，清除失败计数，不产生熔断
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "edu-general-14b",
                "messages": [{"role": "user", "content": "hi"}],
                "auto_failover": False,
            },
        )
        assert resp.status_code == 200
        assert not await fake_redis.exists("gw:fail:http://vllm-general:8100")
        assert not await fake_redis.exists("gw:unhealthy:http://vllm-general:8100")
