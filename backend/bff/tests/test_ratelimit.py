"""限流测试：滑窗逻辑单测（fakeredis）+ 中间件 429 行为。"""

from __future__ import annotations

import fakeredis.aioredis

from app.ratelimit import SlidingWindowLimiter
from tests.conftest import auth_headers, register_and_login


async def test_sliding_window_allows_within_limit():
    limiter = SlidingWindowLimiter(fakeredis.aioredis.FakeRedis(decode_responses=True))
    assert [await limiter.allow("u:1", 3) for _ in range(3)] == [True, True, True]


async def test_sliding_window_blocks_over_limit():
    limiter = SlidingWindowLimiter(fakeredis.aioredis.FakeRedis(decode_responses=True))
    for _ in range(3):
        assert await limiter.allow("u:1", 3)
    assert not await limiter.allow("u:1", 3)


async def test_sliding_window_identities_are_independent():
    limiter = SlidingWindowLimiter(fakeredis.aioredis.FakeRedis(decode_responses=True))
    for _ in range(3):
        assert await limiter.allow("u:1", 3)
    # 其他身份不受 u:1 超限影响
    assert await limiter.allow("u:2", 3)


async def test_middleware_returns_429_over_limit(client, app):
    # 把免费档收紧到 2 RPM，触发限流
    app.state.rate_limits = {"free": 2, "paid": 60, "teacher": 100}
    token = await register_and_login(client, "13800000003")
    headers = auth_headers(token)

    assert (await client.get("/api/v1/users/me", headers=headers)).status_code == 200
    assert (await client.get("/api/v1/users/me", headers=headers)).status_code == 200
    resp = await client.get("/api/v1/users/me", headers=headers)
    assert resp.status_code == 429
    assert resp.json()["code"] == "rate_limited"
    assert resp.headers["Retry-After"] == "60"


async def test_middleware_skips_healthz(client, app):
    app.state.rate_limits = {"free": 0, "paid": 0, "teacher": 0}
    for _ in range(3):
        assert (await client.get("/healthz")).status_code == 200
