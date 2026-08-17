"""异步 Redis 客户端工厂（redis-py async）。"""

from __future__ import annotations

import redis.asyncio as aioredis

from .config import get_settings

_client: aioredis.Redis | None = None


def create_redis(url: str | None = None, **kwargs) -> aioredis.Redis:
    """创建一个新的异步 Redis 客户端（连接惰性建立）。"""
    kwargs.setdefault("decode_responses", True)
    return aioredis.from_url(url or get_settings().redis_url, **kwargs)


def get_redis() -> aioredis.Redis:
    """取进程级共享客户端。"""
    global _client
    if _client is None:
        _client = create_redis()
    return _client


async def close_redis() -> None:
    """关闭共享客户端（应用关停时调用）。"""
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None
