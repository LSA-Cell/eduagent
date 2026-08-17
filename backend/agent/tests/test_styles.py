"""Output Styles 风格注册表测试（§5.3）。

覆盖：按年级学科命中 tone_prompt 注入 <output_style> 区块 / 未命中不注入 /
前缀确定性不破（同输入 hash 一致）/ 风格只影响表述（审核与事实路径不变）。
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from app.loop.agent_loop import _base_system_prompt
from app.loop.turn_config import TurnConfig
from app.styles.models import OutputStyle
from app.styles.registry import StyleRegistry, format_style_block
from tests.conftest import FakeSession, as_factory


def _seed(session: FakeSession) -> None:
    session.add(OutputStyle(grade_level="grade_7", subject="math",
                            tone_prompt="讲解严谨，步骤分明。", is_active=True))
    session.add(OutputStyle(grade_level="*", subject="*",
                            tone_prompt="语气亲切鼓励。", is_active=True))
    session.add(OutputStyle(grade_level="grade_1", subject="*",
                            tone_prompt="已下线的风格", is_active=False))


class TestRegistry:
    async def test_exact_match_beats_wildcard(self) -> None:
        """精确 (grade, subject) 命中优先于通配。"""
        session = FakeSession()
        _seed(session)
        await session.flush()
        registry = StyleRegistry(as_factory(session))
        await registry.refresh()
        assert registry.tone_prompt("grade_7", "math") == "讲解严谨，步骤分明。"
        assert registry.tone_prompt("grade_3", "english") == "语气亲切鼓励。"  # 兜底通配
        assert registry.tone_prompt("grade_1", "math") == "语气亲切鼓励。"  # 停用行不生效

    async def test_no_hit_returns_none(self) -> None:
        """未命中（无任何行/全停用）返回 None → 不注入。"""
        session = FakeSession()
        registry = StyleRegistry(as_factory(session))
        await registry.refresh()
        assert registry.tone_prompt("grade_7", "math") is None

    def test_cold_cache_none(self) -> None:
        session = FakeSession()
        _seed(session)
        registry = StyleRegistry(as_factory(session))  # 未 refresh
        assert registry.tone_prompt("grade_7", "math") is None

    async def test_cache_ttl(self) -> None:
        """进程内缓存 60s 热生效（TTL 内旧缓存，越过 TTL refresh 后生效）。"""
        session = FakeSession()
        _seed(session)
        await session.flush()
        clock_now = [0.0]
        registry = StyleRegistry(as_factory(session), clock=lambda: clock_now[0])
        await registry.refresh()
        session.add(OutputStyle(grade_level="grade_9", subject="physics",
                                tone_prompt="新风格", is_active=True))
        await session.flush()
        clock_now[0] = 30.0
        assert registry.tone_prompt("grade_9", "physics") == "语气亲切鼓励。"  # 旧缓存
        clock_now[0] = 61.0
        assert registry.tone_prompt("grade_9", "physics") is None  # 过期未刷新
        await registry.refresh()
        assert registry.tone_prompt("grade_9", "physics") == "新风格"


class TestPromptInjection:
    def test_style_block_format(self) -> None:
        block = format_style_block("讲解严谨。")
        assert block.startswith("<output_style>") and block.endswith("</output_style>")
        assert "讲解严谨。" in block
        assert "事实正确性与内容审核标准不受影响" in block  # 红线声明随区块固定携带

    def _config(self, **overrides) -> TurnConfig:
        return TurnConfig(user_id="u1", grade_level="grade_7", subject="math", **overrides)

    def test_injection_into_base_prompt(self) -> None:
        """命中 → <output_style> 区块进入 System Prompt 前缀稳定区。"""
        prompt = _base_system_prompt(self._config(), "讲解严谨，步骤分明。")
        assert "<output_style>" in prompt and "讲解严谨，步骤分明。" in prompt
        # 未命中 → 与旧行为字节一致（无区块）
        assert _base_system_prompt(self._config(), None) == _base_system_prompt(self._config())

    def test_prefix_determinism(self) -> None:
        """前缀确定性：同输入同风格 → system prompt hash 一致（缓存红线 §3.1）。"""
        config = self._config()
        h1 = hashlib.sha256(
            _base_system_prompt(config, "讲解严谨，步骤分明。").encode()
        ).hexdigest()
        h2 = hashlib.sha256(
            _base_system_prompt(config, "讲解严谨，步骤分明。").encode()
        ).hexdigest()
        assert h1 == h2

    async def test_loop_end_to_end_style_injection(self) -> None:
        """Loop 端到端：deps.output_style_lookup 命中 → 模型请求 system 含区块。"""
        from app.loop.agent_loop import edu_agent_loop
        from tests.orchestration_fakes import FakeDeps

        session = FakeSession()
        _seed(session)
        await session.flush()
        registry = StyleRegistry(as_factory(session))

        deps = FakeDeps()
        deps.output_style_lookup = registry.tone_prompt
        deps.output_style_refresh = registry.refresh
        req = SimpleNamespace(user_id="u1", message="你好", session_id=None,
                              subject="math", grade_level="grade_7", model=None)
        [e async for e in edu_agent_loop(req, deps)]
        system_content = deps.gateway.stream_calls[0]["messages"][0]["content"]
        assert "<output_style>" in system_content
        assert "讲解严谨，步骤分明。" in system_content

        # 未命中学科：不注入（system 无区块）
        deps2 = FakeDeps()
        deps2.output_style_lookup = registry.tone_prompt
        deps2.output_style_refresh = registry.refresh
        req2 = SimpleNamespace(user_id="u1", message="你好", session_id=None,
                               subject="chemistry", grade_level=None, model=None)
        [e async for e in edu_agent_loop(req2, deps2)]
        system2 = deps2.gateway.stream_calls[0]["messages"][0]["content"]
        assert "讲解严谨" not in system2  # 精确行未命中
        assert "语气亲切鼓励。" in system2  # ('*','*') 通配兜底命中
