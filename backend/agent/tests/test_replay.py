"""SSE 事件重放测试：append 后 replay_since 无重复无丢失；Redis/内存双通道。"""

from __future__ import annotations

import pytest

from app.events.protocol import EventType, SSESequencer
from app.events.replay import REPLAY_TTL_S, EventReplayStore
from app.events.sse import sse_encode


class FakeRedis:
    """redis.asyncio 鸭子类型替身（list + expire）。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.lists: dict[str, list[str]] = {}
        self.expiry: dict[str, int] = {}
        self.fail = fail

    async def rpush(self, key: str, value: str) -> None:
        if self.fail:
            raise RuntimeError("redis 不可用")
        self.lists.setdefault(key, []).append(value)

    async def expire(self, key: str, ttl_s: int) -> None:
        if self.fail:
            raise RuntimeError("redis 不可用")
        self.expiry[key] = ttl_s

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        if self.fail:
            raise RuntimeError("redis 不可用")
        return list(self.lists.get(key, []))


def _events(seq: SSESequencer, n: int, run_id: str = "r1"):
    return [seq.make(EventType.MESSAGE_DELTA, run_id=run_id, text=f"t{i}") for i in range(n)]


class TestMemoryRingBuffer:
    async def test_replay_since_no_dup_no_loss(self) -> None:
        store = EventReplayStore()
        events = _events(SSESequencer(), 5)
        for e in events:
            await store.append("r1", e)
        replayed = await store.replay_since("r1", 2)
        assert [e.seq for e in replayed] == [3, 4, 5]  # 其后事件，无重复无丢失
        assert await store.replay_since("r1", 5) == []
        assert [e.seq for e in await store.replay_since("r1", 0)] == [1, 2, 3, 4, 5]

    async def test_ring_buffer_evicts_oldest(self) -> None:
        store = EventReplayStore(ring_maxlen=3)
        for e in _events(SSESequencer(), 5):
            await store.append("r1", e)
        replayed = await store.replay_since("r1", 0)
        assert [e.seq for e in replayed] == [3, 4, 5]  # 超出窗口的已淘汰

    async def test_runs_isolated(self) -> None:
        store = EventReplayStore()
        seq = SSESequencer()
        await store.append("ra", seq.make(EventType.RUN_START, run_id="ra", session_id="s"))
        await store.append("rb", seq.make(EventType.RUN_START, run_id="rb", session_id="s"))
        assert len(await store.replay_since("ra", 0)) == 1
        assert len(await store.replay_since("rb", 0)) == 1


class TestRedisBacked:
    async def test_append_persisted_with_ttl(self) -> None:
        redis = FakeRedis()
        store = EventReplayStore(redis)
        for e in _events(SSESequencer(), 3):
            await store.append("r1", e)
        key = "agent:events:r1"
        assert len(redis.lists[key]) == 3
        assert redis.expiry[key] == REPLAY_TTL_S  # TTL 5 分钟
        replayed = await store.replay_since("r1", 1)
        assert [e.seq for e in replayed] == [2, 3]
        assert replayed[0].data["text"] == "t1"

    async def test_redis_outage_falls_back_to_memory(self) -> None:
        redis = FakeRedis(fail=True)
        store = EventReplayStore(redis)
        events = _events(SSESequencer(), 4)
        for e in events:
            await store.append("r1", e)  # 降级内存，不抛错
        replayed = await store.replay_since("r1", 1)
        assert [e.seq for e in replayed] == [2, 3, 4]


class TestSseEncode:
    def test_frame_format_with_id(self) -> None:
        seq = SSESequencer()
        event = seq.make(EventType.RUN_START, run_id="r1", session_id="s1")
        frame = sse_encode(event)
        assert frame.startswith("id: 1\n")
        assert "event: run.start\n" in frame
        assert '"run_id": "r1"' in frame
        assert frame.endswith("\n\n")
