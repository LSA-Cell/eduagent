"""记忆管理器：对话前检索注入，对话后去重写库。

记忆类型（memory_type）：
- preference  显式偏好
- profile     隐式画像
- weakness    薄弱知识点
- general     其它
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from sqlalchemy import text

from eduagent_shared.models import Memory

from app.memory.embeddings import EmbeddingProvider
from app.prompts import BASE_SYSTEM_TEMPLATE, STEP_INSTRUCTION

MEMORY_TYPES = ("preference", "profile", "weakness", "general")
DEFAULT_TOP_K = 5
DEDUP_COSINE_THRESHOLD = 0.92  # 语义去重阈值


@dataclass
class MemoryItem:
    """检索出的记忆条目（与 ORM 解耦，便于测试）。"""

    content: str
    memory_type: str = "general"
    importance: float = 0.5
    distance: Optional[float] = None


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度，纯函数便于单测。"""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _getattr_any(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def _render_block(items: list[str]) -> str:
    return "\n".join(f"- {line}" for line in items) if items else "- （暂无）"


def build_system_prompt(user: Any, memories: Sequence[Any]) -> str:
    """组装注入记忆的 system prompt（纯函数，user/memories 鸭子类型）。

    user 可用字段：name/nickname、grade、subject_preference/subjects。
    memories 元素字段：memory_type、content、importance。
    """
    name = _getattr_any(user, "nickname", "name", default="同学")
    grade = _getattr_any(user, "grade_level", "grade", default="未知")
    subjects = _getattr_any(user, "subject_preference", "default_subject", "subjects", default=None)
    if isinstance(subjects, (list, tuple)):
        subject_preference = "、".join(str(s) for s in subjects)
    else:
        subject_preference = str(subjects) if subjects else "不限"

    by_type: dict[str, list[str]] = {t: [] for t in MEMORY_TYPES}
    for m in memories:
        mtype = _getattr_any(m, "memory_type", "type", default="general")
        content = str(_getattr_any(m, "content", default="")).strip()
        if not content:
            continue
        by_type.setdefault(mtype if mtype in by_type else "general", []).append(content)

    general = by_type.get("general", [])
    memory_block = ""
    if general:
        memory_block = "【其它记忆摘要】\n" + _render_block(general) + "\n"

    return BASE_SYSTEM_TEMPLATE.format(
        student_name=name,
        grade=grade,
        subject_preference=subject_preference,
        weakness_block=_render_block(by_type.get("weakness", [])),
        preference_block=_render_block(by_type.get("preference", [])),
        profile_block=_render_block(by_type.get("profile", [])),
        memory_block=memory_block,
        step_instruction=STEP_INSTRUCTION,
    )


async def retrieve_memories(
    session: Any,
    user_id: str,
    query_embedding: Optional[Sequence[float]] = None,
    top_k: int = DEFAULT_TOP_K,
) -> list[MemoryItem]:
    """检索用户记忆：有 embedding 时按 pgvector `<=>` 余弦距离 top-k，
    否则退化为按重要度 + 最近创建排序。"""
    if query_embedding:
        vec_literal = "[" + ",".join(f"{v:.6f}" for v in query_embedding) + "]"
        stmt = text(
            "SELECT content, memory_type, importance, embedding <=> CAST(:qvec AS vector) AS distance "
            "FROM memories WHERE user_id = :uid "
            "ORDER BY embedding <=> CAST(:qvec AS vector) ASC LIMIT :k"
        )
        rows = (await session.execute(stmt, {"qvec": vec_literal, "uid": user_id, "k": top_k})).all()
        return [
            MemoryItem(content=r.content, memory_type=r.memory_type,
                       importance=float(r.importance or 0.5), distance=float(r.distance))
            for r in rows
        ]
    stmt = text(
        "SELECT content, memory_type, importance FROM memories WHERE user_id = :uid "
        "ORDER BY importance DESC, created_at DESC LIMIT :k"
    )
    rows = (await session.execute(stmt, {"uid": user_id, "k": top_k})).all()
    return [
        MemoryItem(content=r.content, memory_type=r.memory_type, importance=float(r.importance or 0.5))
        for r in rows
    ]


async def load_existing_memory_contents(session: Any, user_id: str) -> list[str]:
    """取用户全部记忆文本（用于精确/归一化去重）。"""
    stmt = text("SELECT content FROM memories WHERE user_id = :uid")
    rows = (await session.execute(stmt, {"uid": user_id})).all()
    return [r.content for r in rows]


def normalize_fact(content: str) -> str:
    """事实文本归一化：去空白、小写，用于精确去重。"""
    return "".join(str(content).split()).lower()


async def dedup_and_save_memories(
    session: Any,
    user_id: str,
    facts: Sequence[dict[str, Any]],
    provider: Optional[EmbeddingProvider] = None,
    existing_contents: Optional[Sequence[str]] = None,
) -> list[Any]:
    """对 LLM 提取的事实去重后写入 memories 表，返回新写入的记忆对象。

    去重策略：先归一化文本精确去重；若有 provider，再对候选做语义相似度去重
    （与本批已接受的事实比对，阈值 DEDUP_COSINE_THRESHOLD）。
    """
    if existing_contents is None:
        existing_contents = await load_existing_memory_contents(session, user_id)
    seen = {normalize_fact(c) for c in existing_contents}
    accepted: list[dict[str, Any]] = []
    accepted_vectors: list[Sequence[float]] = []

    for fact in facts:
        content = str(fact.get("content", "")).strip()
        if not content:
            continue
        key = normalize_fact(content)
        if key in seen:
            continue
        mtype = str(fact.get("type", "general"))
        if mtype not in MEMORY_TYPES:
            mtype = "general"
        importance = float(fact.get("importance", 0.5) or 0.5)
        vector: Optional[list[float]] = None
        if provider is not None:
            vector = (await provider.embed([content]))[0]
            if any(
                cosine_similarity(vector, v) >= DEDUP_COSINE_THRESHOLD for v in accepted_vectors
            ):
                continue
            accepted_vectors.append(vector)
        seen.add(key)
        accepted.append({"content": content, "memory_type": mtype,
                         "importance": importance, "embedding": vector})

    created: list[Any] = []
    for item in accepted:
        memory = Memory(
            user_id=user_id,
            memory_type=item["memory_type"],
            content=item["content"],
            importance=item["importance"],
            embedding=item["embedding"],
            metadata_={"source": "chat"},  # ORM 无 source 列，来源记入 metadata
        )
        session.add(memory)
        created.append(memory)
    if created:
        await session.flush()
    return created
