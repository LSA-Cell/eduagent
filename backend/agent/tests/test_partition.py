"""工具并发分批与执行器测试：分批规则、超时、瘦身、审计（全部离线）。"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.loop.partition import MAX_CONCURRENT_BATCH, partition_tool_calls
from app.loop.turn_config import TurnConfig
from app.tools.base import ToolContext, ToolResult
from app.tools.executor import ToolCallRequest, ToolExecutor

from tests.orchestration_fakes import (
    FakeDeps,
    FakeStorage,
    make_registry,
    make_tool,
)


def _calls(*names: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(name=n, id=f"c{i}") for i, n in enumerate(names)]


class TestPartition:
    def test_consecutive_readonly_single_concurrent_batch(self) -> None:
        registry = make_registry(
            make_tool("r1"), make_tool("r2"), make_tool("r3"),
        )
        batches = partition_tool_calls(_calls("r1", "r2", "r3"), registry)
        assert len(batches) == 1
        assert batches[0].concurrent is True
        assert [c.name for c in batches[0].calls] == ["r1", "r2", "r3"]

    def test_write_tool_splits_batches(self) -> None:
        registry = make_registry(
            make_tool("r1"), make_tool("r2"),
            make_tool("w1", is_read_only=False, is_concurrency_safe=False),
            make_tool("r3"),
        )
        batches = partition_tool_calls(_calls("r1", "r2", "w1", "r3"), registry)
        assert [(b.concurrent, [c.name for c in b.calls]) for b in batches] == [
            (True, ["r1", "r2"]),
            (False, ["w1"]),  # 写工具单独串行批
            (True, ["r3"]),
        ]

    def test_concurrent_batch_cap(self) -> None:
        registry = make_registry(*[make_tool(f"r{i}") for i in range(MAX_CONCURRENT_BATCH + 2)])
        batches = partition_tool_calls(
            _calls(*[f"r{i}" for i in range(MAX_CONCURRENT_BATCH + 2)]), registry
        )
        assert [len(b.calls) for b in batches] == [MAX_CONCURRENT_BATCH, 2]
        assert all(b.concurrent for b in batches)

    def test_readonly_but_not_concurrency_safe_is_serial(self) -> None:
        registry = make_registry(
            make_tool("safe"),
            make_tool("unsafe", is_read_only=True, is_concurrency_safe=False),
        )
        batches = partition_tool_calls(_calls("safe", "unsafe"), registry)
        assert [(b.concurrent, len(b.calls)) for b in batches] == [(True, 1), (False, 1)]

    def test_unknown_tool_treated_as_write(self) -> None:
        registry = make_registry(make_tool("r1"))
        batches = partition_tool_calls(_calls("r1", "ghost", "r1"), registry)
        assert [(b.concurrent, [c.name for c in b.calls]) for b in batches] == [
            (True, ["r1"]), (False, ["ghost"]), (True, ["r1"]),
        ]


def _executor(deps: FakeDeps) -> ToolExecutor:
    return ToolExecutor(
        deps.tool_registry, deps.guardrails, deps,
        audit=lambda fields: deps.audit.write_tool_call(**fields),
    )


def _ctx() -> ToolContext:
    return ToolContext(user_id="u1", run_id="run-1")


class TestExecutor:
    async def test_concurrent_batch_gathers(self) -> None:
        """两个各耗时 0.2s 的只读工具并发执行，总耗时 < 串行。"""
        registry = make_registry(
            make_tool("slow1", delay_s=0.2), make_tool("slow2", delay_s=0.2),
        )
        deps = FakeDeps(registry=registry)
        config = TurnConfig(user_id="u1")
        started = time.monotonic()
        results = await _executor(deps).execute(
            [ToolCallRequest(id="c1", name="slow1"), ToolCallRequest(id="c2", name="slow2")],
            config, _ctx(),
        )
        elapsed = time.monotonic() - started
        assert all(r.ok for r in results)
        assert elapsed < 0.35  # 串行需 ≥0.4s

    async def test_write_tools_run_serially(self) -> None:
        registry = make_registry(
            make_tool("w1", is_read_only=False, delay_s=0.15),
            make_tool("w2", is_read_only=False, delay_s=0.15),
        )
        deps = FakeDeps(registry=registry)
        config = TurnConfig(user_id="u1")
        started = time.monotonic()
        await _executor(deps).execute(
            [ToolCallRequest(id="c1", name="w1"), ToolCallRequest(id="c2", name="w2")],
            config, _ctx(),
        )
        assert time.monotonic() - started >= 0.28  # 串行

    async def test_timeout_fails_and_reinjects(self) -> None:
        """单工具超时（asyncio.wait_for ≤ timeout_s）按失败回注，不挂起。"""
        registry = make_registry(make_tool("hang", delay_s=1.0, timeout_s=0.2))
        deps = FakeDeps(registry=registry)
        config = TurnConfig(user_id="u1")
        started = time.monotonic()
        [result] = await _executor(deps).execute(
            [ToolCallRequest(id="c1", name="hang")], config, _ctx()
        )
        assert time.monotonic() - started < 0.6
        assert result.ok is False
        assert "超时" in result.content
        assert deps.audit.tool_calls[0]["decision"] == "allow"

    async def test_result_slimming_to_storage(self) -> None:
        """超限结果存 tool-results/，content 替换为摘要 + 预签名 URL + 全文长度。"""
        big = ToolResult(content="x" * 5000)
        registry = make_registry(make_tool("big", result=big, max_result_chars=100))
        storage = FakeStorage()
        deps = FakeDeps(registry=registry, storage=storage)
        config = TurnConfig(user_id="u1")
        [result] = await _executor(deps).execute(
            [ToolCallRequest(id="c1", name="big")], config, _ctx()
        )
        assert result.ok
        assert len(storage.objects) == 1
        key = next(iter(storage.objects))
        assert key.startswith("tool-results/run-1/")  # 键含 run_id
        assert storage.objects[key] == "x" * 5000  # 全文在存储里
        assert "https://minio.test/" in result.content
        assert "5000" in result.content  # 全文长度标注
        assert len(result.content) < 1000  # 原文不回注

    async def test_slimming_fallback_truncation(self) -> None:
        """storage 不可用时降级截断 + 标注。"""
        big = ToolResult(content="y" * 5000)
        registry = make_registry(make_tool("big", result=big, max_result_chars=100))
        deps = FakeDeps(registry=registry, storage=FakeStorage(fail=True))
        config = TurnConfig(user_id="u1")
        [result] = await _executor(deps).execute(
            [ToolCallRequest(id="c1", name="big")], config, _ctx()
        )
        assert "已截断" in result.content
        assert "5000" in result.content

    async def test_args_validation_error_reinjected(self) -> None:
        from pydantic import BaseModel, Field

        class StrictArgs(BaseModel):
            n: int = Field(..., description="必填整数")

        registry = make_registry(make_tool("strict", input_model=StrictArgs))
        deps = FakeDeps(registry=registry)
        config = TurnConfig(user_id="u1")
        [result] = await _executor(deps).execute(
            [ToolCallRequest(id="c1", name="strict", arguments={"n": "not-int"}),],
            config, _ctx(),
        )
        assert result.ok is False
        assert "参数校验失败" in result.content
