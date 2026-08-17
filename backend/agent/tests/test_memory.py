"""记忆系统测试：system prompt 组装、相似度、去重写库、检索退化路径。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from eduagent_shared.models import Memory

from app.memory import memory_manager
from app.memory.embeddings import HashEmbeddingProvider, hash_embed
from app.memory.memory_manager import (
    MemoryItem,
    build_system_prompt,
    cosine_similarity,
    normalize_fact,
)
from tests.conftest import FakeSession


class TestBuildSystemPrompt:
    def test_full_assembly(self) -> None:
        user = SimpleNamespace(nickname="小明", grade_level="初三", default_subject="数学")
        memories = [
            MemoryItem(content="分式方程去分母常漏乘", memory_type="weakness", importance=0.9),
            MemoryItem(content="喜欢用例题讲解", memory_type="preference", importance=0.8),
            MemoryItem(content="备战中考", memory_type="profile", importance=0.7),
            MemoryItem(content="每周日晚自习后学习", memory_type="general", importance=0.4),
        ]
        prompt = build_system_prompt(user, memories)
        assert "小明" in prompt and "初三" in prompt and "数学" in prompt
        assert "【薄弱知识点】" in prompt and "- 分式方程去分母常漏乘" in prompt
        assert "【学习偏好】" in prompt and "- 喜欢用例题讲解" in prompt
        assert "【学生画像】" in prompt and "- 备战中考" in prompt
        assert "【其它记忆摘要】" in prompt and "- 每周日晚自习后学习" in prompt
        assert "<step>" in prompt  # 分步讲解标签约定注入

    def test_empty_memories_render_placeholder(self) -> None:
        prompt = build_system_prompt(SimpleNamespace(), [])
        assert prompt.count("- （暂无）") == 3  # 薄弱/偏好/画像三块
        assert "【其它记忆摘要】" not in prompt  # 无 general 记忆时不出现该块
        assert "未知" in prompt  # 缺省年级

    def test_unknown_type_falls_into_general(self) -> None:
        prompt = build_system_prompt(
            SimpleNamespace(grade="高一"),
            [MemoryItem(content="晚自习效率高", memory_type="event")],
        )
        assert "【其它记忆摘要】" in prompt and "晚自习效率高" in prompt

    def test_subjects_list_joined(self) -> None:
        user = SimpleNamespace(name="小红", grade="高一", subjects=["数学", "物理"])
        prompt = build_system_prompt(user, [])
        assert "数学、物理" in prompt


class TestSimilarityAndNormalize:
    def test_cosine_similarity(self) -> None:
        assert cosine_similarity([1, 0], [1, 0]) == 1.0
        assert round(cosine_similarity([1, 0], [0, 1]), 6) == 0.0
        assert cosine_similarity([], [1]) == 0.0

    def test_normalize_fact(self) -> None:
        assert normalize_fact(" 学生 喜欢\n例题 ") == "学生喜欢例题"
        assert normalize_fact("ABC") == "abc"


class TestDedupAndSave:
    @staticmethod
    def _session_with_existing(contents: list[str]) -> FakeSession:
        def handler(stmt: Any, params: Any) -> list[Any] | None:
            if "FROM memories" in str(stmt):
                return [SimpleNamespace(content=c) for c in contents]
            return None

        return FakeSession(sql_handler=handler)

    async def test_exact_dedup_and_save(self) -> None:
        session = self._session_with_existing(["学生喜欢例题讲解"])
        facts = [
            {"type": "preference", "content": "学生喜欢例题讲解", "importance": 0.8},  # 重复
            {"type": "profile", "content": "学生初三，备战中考", "importance": 0.9},
            {"type": "unknown_type", "content": "晚自习效率高"},  # 非法类型回落 general
        ]
        created = await memory_manager.dedup_and_save_memories(
            session, "u-1", facts, provider=HashEmbeddingProvider()
        )
        assert len(created) == 2
        types = {m.memory_type for m in created}
        assert types == {"profile", "general"}
        assert all(isinstance(m, Memory) for m in created)
        assert all(m.embedding and len(m.embedding) == 1536 for m in created)

    async def test_semantic_dedup_within_batch(self) -> None:
        session = self._session_with_existing([])
        # hash 伪向量下同一文本向量相同（相似度 1.0 ≥ 阈值），第二条应被语义去重
        facts = [
            {"type": "profile", "content": "学生基础薄弱"},
            {"type": "profile", "content": "学生基础薄弱"},
        ]
        created = await memory_manager.dedup_and_save_memories(
            session, "u-1", facts, provider=HashEmbeddingProvider()
        )
        assert len(created) == 1

    async def test_empty_and_blank_facts_skipped(self) -> None:
        session = self._session_with_existing([])
        created = await memory_manager.dedup_and_save_memories(
            session, "u-1", [{"type": "profile", "content": "   "}, {}],
            provider=HashEmbeddingProvider(),
        )
        assert created == []


class TestRetrieveMemories:
    async def test_fallback_order_by_importance(self) -> None:
        """无 embedding 时走重要度排序的退化 SQL（由 sql_handler 模拟返回）。"""
        rows = [
            SimpleNamespace(content="a", memory_type="profile", importance=0.9),
            SimpleNamespace(content="b", memory_type="weakness", importance=0.7),
        ]
        session = FakeSession(sql_handler=lambda stmt, params: rows)
        items = await memory_manager.retrieve_memories(session, "u-1")
        assert [i.content for i in items] == ["a", "b"]
        assert items[0].memory_type == "profile"

    async def test_vector_path_uses_distance(self) -> None:
        rows = [SimpleNamespace(content="a", memory_type="profile", importance=0.9,
                                distance=0.12)]
        session = FakeSession(sql_handler=lambda stmt, params: rows)
        items = await memory_manager.retrieve_memories(session, "u-1", query_embedding=hash_embed("x"))
        assert items[0].distance == 0.12
