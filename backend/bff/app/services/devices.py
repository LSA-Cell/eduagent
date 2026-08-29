"""设备会话：refresh token 真吊销 / 单设备登录 / 设备管理。

设计（与 refresh jti 一次性作废同一可用性策略）：
- 每次登录创建设备会话 sid，写入 refresh token 的 ``sid`` claim；
- 会话状态存 Redis hash ``devsess:{sid}``，TTL = refresh token 有效期，每次刷新续期；
- 每用户索引 ``devsess_u:{user_id}``（set  of sid），用于设备列表与全量吊销；
- 登出同时把当前 access token 的 jti 写入黑名单 ``atk_blk:{jti}``（TTL 至其自然过期），
  ``get_current_user`` 校验黑名单，实现「踢下线立即生效」；
- Redis 故障时 fail-open 并告警，避免缓存故障拖垮登录链路（同限流器策略）。
"""

from __future__ import annotations

import time
import uuid

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from eduagent_shared.config import get_settings
from eduagent_shared.logging import get_logger

log = get_logger("bff.devices")

_SESSION_PREFIX = "devsess"
_USER_INDEX_PREFIX = "devsess_u"
_ACCESS_BLK_PREFIX = "atk_blk"


def _ttl_seconds() -> int:
    return get_settings().refresh_token_expire_days * 86400


async def create_session(
    redis: aioredis.Redis,
    *,
    user_id: uuid.UUID,
    device_id: str | None,
    device_name: str | None,
) -> str:
    """创建设备会话，返回 sid；同时登记进用户设备索引。"""
    sid = uuid.uuid4().hex
    now = str(int(time.time()))
    key = f"{_SESSION_PREFIX}:{sid}"
    try:
        await redis.hset(
            key,
            mapping={
                "user_id": str(user_id),
                "device_id": device_id or "",
                "device_name": device_name or "",
                "created_at": now,
                "last_seen": now,
                "revoked": "0",
            },
        )
        await redis.expire(key, _ttl_seconds())
        index_key = f"{_USER_INDEX_PREFIX}:{user_id}"
        await redis.sadd(index_key, sid)
        await redis.expire(index_key, _ttl_seconds())
    except RedisError as exc:
        log.warning("device_session_create_redis_error", error=str(exc))
    return sid


async def is_session_active(redis: aioredis.Redis, sid: str) -> bool:
    """会话存在且未吊销。Redis 故障 fail-open（返回 True）。"""
    try:
        data = await redis.hgetall(f"{_SESSION_PREFIX}:{sid}")
    except RedisError as exc:
        log.warning("device_session_check_redis_error", error=str(exc))
        return True
    if not data:
        return False
    return data.get("revoked") != "1"


async def touch_session(redis: aioredis.Redis, sid: str) -> None:
    """刷新会话活跃时间并续期（refresh 轮换时调用，保持「7 天无活动才过期」语义）。"""
    key = f"{_SESSION_PREFIX}:{sid}"
    try:
        if await redis.exists(key):
            await redis.hset(key, "last_seen", str(int(time.time())))
            await redis.expire(key, _ttl_seconds())
    except RedisError as exc:
        log.warning("device_session_touch_redis_error", error=str(exc))


async def revoke_session(redis: aioredis.Redis, sid: str) -> None:
    """吊销单个会话：标记 revoked，该 sid 签发的 refresh token 全部失效。"""
    key = f"{_SESSION_PREFIX}:{sid}"
    try:
        if await redis.exists(key):
            await redis.hset(key, "revoked", "1")
    except RedisError as exc:
        log.warning("device_session_revoke_redis_error", error=str(exc))


async def revoke_all_sessions(
    redis: aioredis.Redis, user_id: uuid.UUID, *, except_sid: str | None = None
) -> None:
    """吊销用户全部会话（可豁免当前会话，用于「修改密码后保留本机」）。"""
    try:
        sids = await redis.smembers(f"{_USER_INDEX_PREFIX}:{user_id}")
    except RedisError as exc:
        log.warning("device_session_index_redis_error", error=str(exc))
        return
    for sid in sids:
        if sid == except_sid:
            continue
        await revoke_session(redis, sid)


async def list_sessions(
    redis: aioredis.Redis, user_id: uuid.UUID
) -> list[dict[str, str]]:
    """列出用户活跃设备会话（跳过已吊销/过期的，并顺手清理索引）。"""
    index_key = f"{_USER_INDEX_PREFIX}:{user_id}"
    try:
        sids = await redis.smembers(index_key)
    except RedisError as exc:
        log.warning("device_session_list_redis_error", error=str(exc))
        return []
    sessions: list[dict[str, str]] = []
    stale: list[str] = []
    for sid in sids:
        try:
            data = await redis.hgetall(f"{_SESSION_PREFIX}:{sid}")
        except RedisError:
            data = {}
        if not data or data.get("revoked") == "1":
            stale.append(sid)
            continue
        data["sid"] = sid
        sessions.append(data)
    if stale:
        try:
            await redis.srem(index_key, *stale)
        except RedisError:
            pass
    sessions.sort(key=lambda s: s.get("last_seen", "0"), reverse=True)
    return sessions


async def blacklist_access_jti(
    redis: aioredis.Redis, jti: str, ttl_seconds: int
) -> None:
    """把 access token jti 写入黑名单直到其自然过期（登出/踢下线用）。"""
    if ttl_seconds <= 0:
        return
    try:
        await redis.setex(f"{_ACCESS_BLK_PREFIX}:{jti}", ttl_seconds, "1")
    except RedisError as exc:
        log.warning("access_blacklist_mark_redis_error", error=str(exc))


async def is_access_blacklisted(redis: aioredis.Redis, jti: str) -> bool:
    """access jti 是否在黑名单。Redis 故障 fail-open（返回 False）。"""
    try:
        return bool(await redis.exists(f"{_ACCESS_BLK_PREFIX}:{jti}"))
    except RedisError as exc:
        log.warning("access_blacklist_check_redis_error", error=str(exc))
        return False
