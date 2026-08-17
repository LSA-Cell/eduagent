"""RAG 知识库：文档解析分块入库 + 混合检索（向量 + 关键词 + 学科过滤）。

分块策略按学科区分：
- 数学：按题号边界切分，保留完整题目与选项块；
- 英语：按段落切分并合并到长度上限；
- 通用：500 字滑动窗口，重叠 100 字。

混合检索：pgvector 余弦距离 + ILIKE 关键词打分，加权融合 top-k，返回带引用的上下文。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import httpx
from sqlalchemy import text

from eduagent_shared.models import KbChunk

CHUNK_SIZE = 500
CHUNK_OVERLAP = 100
EN_PARA_MAX = 500
DEFAULT_VECTOR_WEIGHT = 0.6


# ---------------------------------------------------------------------------
# 分块策略（纯函数，可单测）
# ---------------------------------------------------------------------------
_MATH_QUESTION_START = re.compile(
    r"(?m)^\s*(?:第\s*\d+\s*题|\d+\s*[\.、）\)]|【[^】]+】)"
)


def chunk_math(text: str) -> list[str]:
    """数学：按题号切分，保证每块是完整题目（含选项）。"""
    text = text.strip()
    if not text:
        return []
    matches = list(_MATH_QUESTION_START.finditer(text))
    if not matches:
        return chunk_general(text)
    chunks: list[str] = []
    if matches[0].start() > 0:  # 题目前的引导文字（标题/说明）
        head = text[: matches[0].start()].strip()
        if head:
            chunks.append(head)
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.start(): end].strip()
        if block:
            chunks.append(block)
    return chunks


def chunk_english(text: str) -> list[str]:
    """英语：按段落切分，短段落合并到 EN_PARA_MAX 以内。"""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buffer = ""
    for para in paragraphs:
        if buffer and len(buffer) + len(para) + 1 > EN_PARA_MAX:
            chunks.append(buffer)
            buffer = para
        else:
            buffer = f"{buffer}\n{para}" if buffer else para
    if buffer:
        chunks.append(buffer)
    # 超长单段兜底走滑动窗口
    result: list[str] = []
    for chunk in chunks:
        if len(chunk) > CHUNK_SIZE * 2:
            result.extend(chunk_general(chunk))
        else:
            result.append(chunk)
    return result


def chunk_general(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """通用：size 字滑动窗口，overlap 字重叠。"""
    text = text.strip()
    if not text:
        return []
    if size <= overlap:
        raise ValueError("size 必须大于 overlap")
    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunks.append(text[start: start + size])
        start += size - overlap
    return chunks


def chunk_by_subject(text: str, subject: Optional[str] = None) -> list[str]:
    """按学科选择分块策略。"""
    key = (subject or "").strip().lower()
    if key in {"数学", "math", "maths", "mathematics"}:
        return chunk_math(text)
    if key in {"英语", "english", "en"}:
        return chunk_english(text)
    return chunk_general(text)


# ---------------------------------------------------------------------------
# 文档解析入库（Celery 任务调用）
# ---------------------------------------------------------------------------
async def call_parse_document(
    object_key: str, multimodal_url: str, token: str, file_type: Optional[str] = None,
    timeout: float = 300.0,
) -> str:
    """调多模态服务解析文档文本（/parse-document，图片类走 /ocr）。

    object_key 为 MinIO 对象键（KnowledgeDocument.object_key）；多模态服务自行取文件。
    """
    path = "/ocr" if (file_type or "").lower() in {"jpg", "jpeg", "png", "webp", "image"} \
        else "/parse-document"
    headers = {"X-Internal-Token": token, "Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(
        base_url=multimodal_url.rstrip("/"), headers=headers, timeout=timeout
    ) as client:
        resp = await client.post(
            path, json={"object_key": object_key, "file_url": object_key, "file_type": file_type}
        )
        resp.raise_for_status()
        data = resp.json()
    text_out = data.get("text") or data.get("content") or ""
    if not text_out.strip():
        raise RuntimeError(f"多模态服务未返回文本: {str(data)[:200]}")
    return text_out


async def parse_and_index_document(
    session: Any,
    document: Any,
    parsed_text: str,
    provider: Any,
) -> int:
    """分块 → embedding → 写入 kb_chunks，返回 chunk 数。

    KbChunk 无 subject 列，学科过滤走 knowledge_documents 关联查询。
    """
    chunks = chunk_by_subject(parsed_text, getattr(document, "subject", None))
    if not chunks:
        return 0
    vectors = await provider.embed(chunks)
    for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
        session.add(KbChunk(
            document_id=document.id,
            user_id=document.user_id,
            chunk_index=index,
            content=chunk,
            embedding=vector,
        ))
    await session.flush()
    return len(chunks)


# ---------------------------------------------------------------------------
# 混合检索
# ---------------------------------------------------------------------------
@dataclass
class RetrievedChunk:
    """带引用的检索结果。"""

    content: str
    score: float
    citation: dict[str, Any] = field(default_factory=dict)


def fuse_results(
    vector_hits: Sequence[dict[str, Any]],
    keyword_hits: Sequence[dict[str, Any]],
    vector_weight: float = DEFAULT_VECTOR_WEIGHT,
    top_k: int = 5,
) -> list[RetrievedChunk]:
    """向量命中（distance 越小越好）与关键词命中（kw_score 越大越好）加权融合（纯函数）。"""
    scores: dict[str, dict[str, Any]] = {}
    max_kw = max((h.get("kw_score", 0.0) for h in keyword_hits), default=0.0) or 1.0
    for hit in vector_hits:
        sim = 1.0 / (1.0 + max(float(hit.get("distance", 1.0)), 0.0))
        scores[str(hit["id"])] = {"data": hit, "vec": sim, "kw": 0.0}
    for hit in keyword_hits:
        entry = scores.setdefault(str(hit["id"]), {"data": hit, "vec": 0.0, "kw": 0.0})
        entry["kw"] = float(hit.get("kw_score", 0.0)) / max_kw
    fused = [
        RetrievedChunk(
            content=e["data"].get("content", ""),
            score=vector_weight * e["vec"] + (1 - vector_weight) * e["kw"],
            citation={
                "document_id": e["data"].get("document_id"),
                "document_title": e["data"].get("document_title"),
                "chunk_index": e["data"].get("chunk_index"),
            },
        )
        for e in scores.values()
    ]
    fused.sort(key=lambda c: c.score, reverse=True)
    return fused[:top_k]


async def hybrid_search(
    session: Any,
    query: str,
    query_embedding: Optional[Sequence[float]] = None,
    subject: Optional[str] = None,
    top_k: int = 5,
    vector_weight: float = DEFAULT_VECTOR_WEIGHT,
    candidate_size: int = 20,
) -> list[RetrievedChunk]:
    """混合检索：向量余弦 + ILIKE 关键词 + 学科过滤，返回带引用 top-k。"""
    subject_filter = "AND d.subject = :subject" if subject else ""
    params: dict[str, Any] = {"q": query, "k": candidate_size}
    if subject:
        params["subject"] = subject

    vector_hits: list[dict[str, Any]] = []
    if query_embedding:
        params["qvec"] = "[" + ",".join(f"{v:.6f}" for v in query_embedding) + "]"
        vector_sql = text(
            "SELECT c.id, c.content, c.chunk_index, c.document_id, d.title AS document_title, "
            "c.embedding <=> CAST(:qvec AS vector) AS distance "
            "FROM kb_chunks c JOIN knowledge_documents d ON d.id = c.document_id "
            f"WHERE true {subject_filter} "
            "ORDER BY c.embedding <=> CAST(:qvec AS vector) ASC LIMIT :k"
        )
        rows = (await session.execute(vector_sql, params)).all()
        vector_hits = [
            {"id": r.id, "content": r.content, "chunk_index": r.chunk_index,
             "document_id": r.document_id, "document_title": r.document_title,
             "distance": float(r.distance)}
            for r in rows
        ]

    keyword_sql = text(
        "SELECT c.id, c.content, c.chunk_index, c.document_id, d.title AS document_title, "
        "(LENGTH(c.content) - LENGTH(REPLACE(LOWER(c.content), LOWER(:q), ''))) "
        "/ GREATEST(LENGTH(:q), 1) AS kw_score "
        "FROM kb_chunks c JOIN knowledge_documents d ON d.id = c.document_id "
        f"WHERE c.content ILIKE '%' || :q || '%' {subject_filter} "
        "ORDER BY kw_score DESC LIMIT :k"
    )
    rows = (await session.execute(keyword_sql, params)).all()
    keyword_hits = [
        {"id": r.id, "content": r.content, "chunk_index": r.chunk_index,
         "document_id": r.document_id, "document_title": r.document_title,
         "kw_score": float(r.kw_score or 0.0)}
        for r in rows
    ]

    return fuse_results(vector_hits, keyword_hits, vector_weight=vector_weight, top_k=top_k)
