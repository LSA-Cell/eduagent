"""仓储层：UserApiKey 读取与额度累加。

- 协议（Protocol）定义接口，便于测试用内存实现替换（dependency override），
  避免测试依赖真实 PostgreSQL（模型含 UUID/JSONB/Vector 等 PG 专属类型，无法跑 SQLite）。
- SqlAlchemyApiKeyStore 为生产实现；InMemoryApiKeyStore 为测试实现，
  属性名与 ORM 模型保持一致。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from eduagent_shared.models import UserApiKey


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ApiKeyStore(Protocol):
    async def get_active(self, user_id: uuid.UUID, provider: str) -> Any | None: ...
    async def add_used_budget(self, key_id: uuid.UUID, cents: int) -> int: ...
    async def list_for_user(self, user_id: uuid.UUID) -> list[Any]: ...


class SqlAlchemyApiKeyStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active(
        self, user_id: uuid.UUID, provider: str
    ) -> UserApiKey | None:
        result = await self.session.execute(
            select(UserApiKey).where(
                UserApiKey.user_id == user_id,
                UserApiKey.provider == provider,
                UserApiKey.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def add_used_budget(self, key_id: uuid.UUID, cents: int) -> int:
        """原子累加 used_budget（防并发请求互相覆盖），返回累加后的值。"""
        result = await self.session.execute(
            update(UserApiKey)
            .where(UserApiKey.id == key_id)
            .values(used_budget=UserApiKey.used_budget + cents)
            .returning(UserApiKey.used_budget)
        )
        new_value = result.scalar_one()
        await self.session.commit()
        return int(new_value)

    async def list_for_user(self, user_id: uuid.UUID) -> list[UserApiKey]:
        result = await self.session.execute(
            select(UserApiKey)
            .where(UserApiKey.user_id == user_id)
            .order_by(UserApiKey.created_at.desc())
        )
        return list(result.scalars())


@dataclass
class ApiKeyRecord:
    user_id: uuid.UUID
    provider: str
    encrypted_key: str
    monthly_budget: int | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    used_budget: int = 0
    is_active: bool = True
    created_at: datetime = field(default_factory=_utcnow)


class InMemoryApiKeyStore:
    """测试用内存实现。"""

    def __init__(self) -> None:
        self._records: dict[uuid.UUID, ApiKeyRecord] = {}

    async def create(self, **kwargs) -> ApiKeyRecord:
        record = ApiKeyRecord(**kwargs)
        self._records[record.id] = record
        return record

    async def get_active(
        self, user_id: uuid.UUID, provider: str
    ) -> ApiKeyRecord | None:
        return next(
            (
                r
                for r in self._records.values()
                if r.user_id == user_id and r.provider == provider and r.is_active
            ),
            None,
        )

    async def add_used_budget(self, key_id: uuid.UUID, cents: int) -> int:
        record = self._records[key_id]
        record.used_budget += cents
        return record.used_budget

    async def list_for_user(self, user_id: uuid.UUID) -> list[ApiKeyRecord]:
        return [r for r in self._records.values() if r.user_id == user_id]
