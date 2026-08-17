"""四级上下文压缩管线测试：触发顺序、保护清单、失败不裸丢（全部离线）。"""

from __future__ import annotations

import pytest

from app.context import autocompact, microcompact, reactive, slim, token_budget
from app.context.autocompact import BOUNDARY_MARKER, CompactionError

from tests.orchestration_fakes import ScriptedGateway


def _msg(role: str, content: str, **kw):
    return {"role": role, "content": content, **kw}


def _tool_msg(name: str, content: str, call_id: str, **kw):
    return _msg("tool", content, name=name, tool_call_id=call_id, **kw)


class TestSlimL1:
    def test_old_long_tool_result_slimmed(self) -> None:
        messages = [
            _msg("system", "系统"),
            _msg("user", "旧问题"),
            _tool_msg("ocr_extract", "长文本" * 2000, "t1"),
            _msg("assistant", "旧回答"),
            _tool_msg("calculator", "42", "t2"),  # 最近一次工具结果（受保护）
            _msg("user", "当前问题"),
        ]
        result, audit = slim.slim_tool_results(messages, current_turn_start=5)
        assert "已瘦身" in result[2]["content"]
        assert "t1" in result[2]["content"]  # 按 tool_use_id 定位可追溯
        assert result[4]["content"] == "42"  # 最近工具结果全文保留
        assert audit["level"] == "slim"
        assert audit["tokens_after"] < audit["tokens_before"]

    def test_protection_list_untouched(self) -> None:
        long_text = "x" * 5000
        messages = [
            _msg("system", "系统\n<learner_memory>\n- 偏好\n</learner_memory>"),
            _tool_msg("ocr", long_text, "t1", pinned=True),  # 收藏/重要
            _msg("assistant", "旧回答"),
            _tool_msg("web", long_text, "t2"),  # 最近一次工具结果全文
            _msg("user", "当前问题"),  # 当前 turn（下标 4 起）
        ]
        result, audit = slim.slim_tool_results(messages, current_turn_start=4)
        assert result[0]["content"] == messages[0]["content"]  # 记忆区块
        assert result[1]["content"] == long_text  # pinned
        assert result[3]["content"] == long_text  # 最近工具结果
        assert audit is None  # 无可压缩对象

    def test_no_op_returns_none_audit(self) -> None:
        messages = [_msg("user", "短"), _tool_msg("calc", "42", "t1")]
        result, audit = slim.slim_tool_results(messages, current_turn_start=1)
        assert audit is None and result == messages


class TestMicrocompactL2:
    def test_keeps_only_recent_k_full(self) -> None:
        messages = [_msg("system", "s")] + [
            _tool_msg(f"t{i}", f"结果{i}", f"c{i}") for i in range(5)
        ] + [_msg("user", "当前")]
        result, audit = microcompact.microcompact(
            messages, current_turn_start=6, keep_full=2
        )
        compacted = [m for m in result if m.get("microcompacted")]
        # 5 条旧工具结果中，最后一条受"最近一次工具结果"保护，可压缩 4 条留 2 条
        assert len(compacted) == 2
        assert all("已压缩" in m["content"] for m in compacted)
        assert result[3]["content"] == "结果2"
        assert result[4]["content"] == "结果3"
        assert result[5]["content"] == "结果4"  # 最近一次工具结果全文保护
        assert audit["level"] == "microcompact"

    def test_below_threshold_no_op(self) -> None:
        messages = [_tool_msg("a", "1", "c1"), _tool_msg("b", "2", "c2"), _msg("user", "u")]
        result, audit = microcompact.microcompact(messages, current_turn_start=2, keep_full=3)
        assert audit is None and result == messages


class TestAutocompactL3:
    async def test_summarizes_with_boundary_marker(self) -> None:
        old = [
            _msg("system", "系统"),
            *[_msg("user" if i % 2 == 0 else "assistant", f"第{i}轮对话" * 10) for i in range(10)],
        ]
        current = [_msg("user", "当前问题")]
        messages = old + current
        gateway = ScriptedGateway(default_summary="前情摘要：讨论了数学。")

        result, audit = await autocompact.autocompact(
            messages, gateway=gateway, current_turn_start=len(old), keep_recent=6
        )

        boundary = [m for m in result if m.get(BOUNDARY_MARKER)]
        assert len(boundary) == 1
        assert "前情摘要" in boundary[0]["content"]
        assert result[0]["content"] == "系统"  # 保护消息保留
        assert result[-1]["content"] == "当前问题"  # 当前 turn 保留原文
        assert audit["level"] == "autocompact"
        assert audit["tokens_after"] < audit["tokens_before"]
        assert audit["summary"] == "前情摘要：讨论了数学。"
        assert len(result) < len(messages)

    async def test_gateway_failure_raises_without_dropping(self) -> None:
        """摘要生成失败 → 抛 CompactionError，原消息列表不被修改（禁止裸丢）。"""
        messages = [_msg("user", f"内容{i}" * 20) for i in range(10)]
        snapshot = [dict(m) for m in messages]
        gateway = ScriptedGateway(chat_results=[RuntimeError("网关 500")])

        with pytest.raises(CompactionError):
            await autocompact.autocompact(
                messages, gateway=gateway, current_turn_start=9, keep_recent=6
            )
        assert messages == snapshot

    async def test_nothing_compressible_is_noop(self) -> None:
        messages = [_msg("system", "s"), _msg("user", "当前")]
        gateway = ScriptedGateway()
        result, audit = await autocompact.autocompact(
            messages, gateway=gateway, current_turn_start=1
        )
        assert audit["summary"] == "无可压缩内容"
        assert result == messages
        assert gateway.chat_calls == []  # 没有浪费摘要调用


class TestReactiveL4:
    async def test_reactive_keeps_only_two_recent(self) -> None:
        messages = [_msg("system", "s")] + [
            _msg("user" if i % 2 else "assistant", f"m{i}" * 30) for i in range(10)
        ]
        gateway = ScriptedGateway(default_summary="响应式摘要")
        result, audit = await reactive.reactive_compact(
            messages, gateway=gateway, current_turn_start=len(messages)
        )
        assert audit["level"] == "reactive"
        tail_contents = [m["content"] for m in result[-2:]]
        assert tail_contents == [m["content"] for m in messages[-2:]]
        assert any(m.get(BOUNDARY_MARKER) for m in result)

    async def test_reactive_raises_when_no_room(self) -> None:
        messages = [_msg("user", "只此一条")]
        gateway = ScriptedGateway()
        with pytest.raises(CompactionError):
            await reactive.reactive_compact(
                messages, gateway=gateway, current_turn_start=0
            )


class TestTriggerOrder:
    def test_window_threshold(self) -> None:
        small = [_msg("user", "短")]
        big = [_msg("user", "长" * 100_000)]
        assert not token_budget.needs_autocompact(small, window_tokens=32_000, buffer_tokens=13_000)
        assert token_budget.needs_autocompact(big, window_tokens=32_000, buffer_tokens=13_000)

    def test_pipeline_order_slim_then_microcompact(self) -> None:
        """同一批消息先 L1 再 L2，两级各司其职、顺序固定。"""
        messages = [_msg("system", "s")] + [
            _tool_msg(f"t{i}", "长" * 3000, f"c{i}") for i in range(6)
        ] + [_msg("user", "当前")]
        after_l1, audit1 = slim.slim_tool_results(messages, current_turn_start=7)
        assert audit1["level"] == "slim"
        after_l2, audit2 = microcompact.microcompact(after_l1, current_turn_start=7, keep_full=2)
        assert audit2["level"] == "microcompact"
        assert audit2["tokens_before"] <= audit1["tokens_after"]  # L2 在 L1 结果上继续
