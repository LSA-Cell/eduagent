"""FastAPI 依赖：配置、DB 会话、内部服务鉴权。

鉴权约定：本服务只对 BFF 暴露，不直接面向 C 端。BFF 在每个请求携带
`X-Internal-Token`（内部服务令牌）与 `X-User-Id`（目标用户）。
"""

from __future__ import annotations

from functools import lru_cache
from typing import AsyncGenerator

from fastapi import Depends, Header, HTTPException

from eduagent_shared.config import Settings
from eduagent_shared.db import get_session
from eduagent_shared.security.internal import InternalTokenError, verify_internal_token


@lru_cache
def get_settings() -> Settings:
    """读取全局配置（字段与 .env.example 对齐，属性名为小写）。"""
    return Settings()


async def get_db() -> AsyncGenerator:
    """请求级异步 DB 会话（委托 shared.db.get_session）。"""
    async for session in get_session():
        yield session


async def require_internal_auth(
    x_internal_token: str = Header(default="", alias="X-Internal-Token"),
    settings: Settings = Depends(get_settings),
) -> None:
    """校验 BFF 传来的内部服务令牌，失败一律 401。"""
    try:
        verify_internal_token(x_internal_token, expected=settings.internal_service_token)
    except InternalTokenError as exc:
        raise HTTPException(status_code=401, detail="invalid internal token") from exc


async def get_user_id(
    x_user_id: str = Header(default="", alias="X-User-Id"),
) -> str:
    """BFF 透传的目标用户 ID（终端用户鉴权已由 BFF 完成）。"""
    if not x_user_id:
        raise HTTPException(status_code=400, detail="missing X-User-Id header")
    return x_user_id


# ---------------------------------------------------------------------------
# LLM / 工具 / Embedding 依赖（路由层注入，测试可用 dependency_overrides 替换）
# ---------------------------------------------------------------------------
def get_llm(settings: Settings = Depends(get_settings)):
    """推理网关客户端（构建廉价：httpx.AsyncClient 按调用创建）。"""
    from app.services.llm_client import LLMClient, get_llm_client

    client: LLMClient = get_llm_client(settings)
    return client


def get_tool_registry():
    """进程级工具注册表（import app.tools 触发内置工具注册）。"""
    import app.tools  # noqa: F401 - 副作用：注册内置工具
    from app.tools.base import ToolRegistry, registry

    checked: ToolRegistry = registry
    return checked


def get_embedding_provider(settings: Settings = Depends(get_settings), llm=Depends(get_llm)):
    """Embedding 提供方（默认走推理网关；AGENT_EMBEDDING_PROVIDER=hash 时本地伪向量）。"""
    from app.memory.embeddings import EmbeddingProvider
    from app.memory.embeddings import get_embedding_provider as _build

    provider: EmbeddingProvider = _build(settings, llm)
    return provider


# ---------------------------------------------------------------------------
# 编排层 Deps（新 SSE 端点用；测试可 dependency_overrides 替换为 FakeDeps）
# ---------------------------------------------------------------------------
@lru_cache
def get_orchestration_deps():
    """DefaultDeps 进程级单例。

    必须单例：replay store（Last-Event-ID 重放）、pending_confirms（ask 确认
    闭环）、task_dedup（任务幂等）都要求跨请求共享同一实例。
    """
    from app.loop.deps_protocol import DefaultDeps

    return DefaultDeps()
