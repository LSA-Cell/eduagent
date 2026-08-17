"""异步数据库引擎 / 会话工厂与 RLS 辅助。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(
    database_url: str | None = None,
    *,
    echo: bool = False,
    pool_size: int = 10,
    max_overflow: int = 20,
) -> AsyncEngine:
    """初始化全局异步引擎（惰性连接，进程启动时不会真正连库）。"""
    global _engine, _session_factory
    _engine = create_async_engine(
        database_url or get_settings().database_url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_engine() -> AsyncEngine:
    """取全局引擎；未初始化时按默认配置初始化。"""
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """取全局会话工厂。"""
    if _session_factory is None:
        init_engine()
    assert _session_factory is not None
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：请求级异步会话。"""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def dispose_engine() -> None:
    """关闭连接池（应用关停时调用）。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def set_rls_user(session: AsyncSession, user_id: uuid.UUID | str) -> None:
    """设置行级安全上下文：当前请求的数据库用户。

    等价于 ``SET LOCAL app.current_user_id = '<uuid>'``；使用 set_config(..., true)
    以支持参数绑定（SET 语句不接受绑定参数），避免 SQL 注入。
    需在事务内调用；依赖 infra/postgres/init.sql 中各表的 RLS 策略读取该变量。
    """
    await session.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(user_id)},
    )
