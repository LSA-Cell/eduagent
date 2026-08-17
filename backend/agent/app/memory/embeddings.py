"""Embedding 提供方抽象。

优先走推理网关 /v1/embeddings；网关不可用或离线环境（如单测）可切换为
本地哈希伪向量（确定性、仅用于去重/测试，不具备真实语义）。
通过环境变量 AGENT_EMBEDDING_PROVIDER=gateway|hash 控制，默认 gateway。
"""

from __future__ import annotations

import hashlib
import math
import os
import random
from typing import Protocol, Sequence

from eduagent_shared.config import Settings

EMBEDDING_DIM = 1536  # 与 KbChunk/Memory 的 Vector(1536) 对齐


class EmbeddingProvider(Protocol):
    """embedding 提供方接口。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """返回与输入等长的向量列表，维度 EMBEDDING_DIM。"""
        ...


class GatewayEmbeddingProvider:
    """走推理网关 /v1/embeddings 的真实语义向量。"""

    def __init__(self, llm: "object") -> None:
        # llm 为 LLMClient（鸭子类型，避免循环依赖）
        self._llm = llm

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = await self._llm.embed(texts)  # type: ignore[attr-defined]
        return [_fit_dim(v) for v in vectors]


class HashEmbeddingProvider:
    """本地哈希伪向量：确定性生成，供离线/降级/测试使用。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [hash_embed(t) for t in texts]


def hash_embed(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """用文本哈希作随机种子生成确定性的单位伪向量。"""
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    vec = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _fit_dim(vec: Sequence[float], dim: int = EMBEDDING_DIM) -> list[float]:
    """维度兜底：不足补零，超出截断。"""
    values = [float(v) for v in vec]
    if len(values) >= dim:
        return values[:dim]
    return values + [0.0] * (dim - len(values))


def get_embedding_provider(settings: Settings, llm: "object | None" = None) -> EmbeddingProvider:
    """按环境选择 provider：AGENT_EMBEDDING_PROVIDER=hash 时强制本地伪向量。"""
    mode = os.environ.get("AGENT_EMBEDDING_PROVIDER", "gateway").lower()
    if mode == "hash" or llm is None:
        return HashEmbeddingProvider()
    return GatewayEmbeddingProvider(llm)
