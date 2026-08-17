"""记忆并发预取与注入测试：不阻塞首 Token、300ms 超时降级、区块格式化。"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.loop.agent_loop import edu_agent_loop
from app.loop.deps_protocol import GatewayEvent
from app.memory import inject, prefetch

from tests.orchestration_fakes import FakeDeps, ScriptedGateway
from tests.test_agent_loop import _collect, _req

SLOW_MEMORY_S = 1.0  # 慢检索：明显超过 300ms 超时
FIRST_TOKEN_BUDGET_S = 0.6  # 宽松计时断言：首 token 不得被慢预取拖住


class TestPrefetch:
    async def test_timeout_degrades_to_empty(self) -> None:
        async def slow() -> list:
            await asyncio.sleep(SLOW_MEMORY_S)
            return [{"content": "x"}]

        task = asyncio.create_task(slow())
        started = time.monotonic()
        result = await prefetch.await_with_timeout(task, timeout_ms=100)
        assert result == []
        assert time.monotonic() - started < 0.5
        task.cancel()

    async def test_result_returned_before_timeout(self) -> None:
        deps = FakeDeps(memories=[{"id": "m1", "content": "喜欢例题", "memory_type": "preference"}])
        task = prefetch.prefetch_memories(deps, "u1", "数学")
        result = await prefetch.await_with_timeout(task, 300)
        assert [m["content"] for m in result] == ["喜欢例题"]

    async def test_failure_degrades_to_empty(self) -> None:
        deps = FakeDeps(memories=RuntimeError("pgvector 宕机"))
        task = prefetch.prefetch_memories(deps, "u1", "数学")
        assert await prefetch.await_with_timeout(task, 300) == []


class TestInjectFormat:
    def test_block_format_and_ids(self) -> None:
        items = [
            {"id": "m1", "content": "目标：中考数学 110+", "memory_type": "preference"},
            {"id": "m2", "content": "薄弱：二次函数", "memory_type": "weakness"},
        ]
        block, ids = inject.format_memory_block(items)
        assert block.startswith("<learner_memory>") and block.endswith("</learner_memory>")
        assert "目标：中考数学 110+" in block and "二次函数" in block
        assert ids == ["m1", "m2"]

    def test_priority_truncation(self) -> None:
        items = [
            {"id": "g1", "content": "一般" * 200, "memory_type": "general", "importance": 0.1},
            {"id": "p1", "content": "偏好：先讲定义", "memory_type": "preference", "importance": 0.9},
        ]
        block, ids = inject.format_memory_block(items, max_tokens=60)
        assert "偏好：先讲定义" in block  # 高优先级保留
        assert "g1" not in ids  # 低优先级被截断
        assert "截断" in block

    def test_empty_items(self) -> None:
        assert inject.format_memory_block([]) == ("", [])


class TestPrefetchInLoop:
    async def test_slow_prefetch_does_not_block_first_token(self) -> None:
        """慢检索（1s）下首 token 仍在超时窗口内到达，且降级为空注入。"""
        memories = (SLOW_MEMORY_S, [{"id": "m1", "content": "x"}])
        deps = FakeDeps(
            gateway=ScriptedGateway(default_script=[GatewayEvent(text="答", finish_reason="stop")]),
            memories=memories,
        )
        started = time.monotonic()
        first_delta_at = None
        events = []
        async for event in edu_agent_loop(_req(), deps):
            events.append(event)
            if event.event == "message.delta" and first_delta_at is None:
                first_delta_at = time.monotonic() - started
        assert first_delta_at is not None
        assert first_delta_at < FIRST_TOKEN_BUDGET_S  # 不被 1s 慢检索阻塞
        assert "memory.used" not in [e.event for e in events]  # 超时降级空注入
        assert events[-1].data["finish_reason"] == "stop"

    async def test_fast_prefetch_injected_and_audited(self) -> None:
        deps = FakeDeps(
            gateway=ScriptedGateway(default_script=[GatewayEvent(text="答", finish_reason="stop")]),
            memories=[{"id": "m1", "content": "偏好：多举例子", "memory_type": "preference"}],
        )
        events = await _collect(_req(), deps)
        used = [e for e in events if e.event == "memory.used"]
        assert used and used[0].data["memory_ids"] == ["m1"]
        # 注入进了 system prompt
        system_message = deps.gateway.stream_calls[0]["messages"][0]
        assert "<learner_memory>" in system_message["content"]
        assert "多举例子" in system_message["content"]
