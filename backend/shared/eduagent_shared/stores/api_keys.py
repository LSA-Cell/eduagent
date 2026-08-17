"""user_api_keys 域仓储（BFF 与 Proxy 共用的一份实现）。

合并自原 ``bff/app/services/store.py`` 与 ``proxy/app/services/store.py``
两份重复实现：

- BFF 负责 Key 生命周期：create / list_for_user / delete；
- Proxy 负责推理计量：get_active / add_used_budget（原子累加防并发覆盖）。

测试策略：InMemoryApiKeyStore 为本地替代品，属性名与 ORM 模型保持一致，
使 Pydantic 的 from_attributes 序列化对两者通用；DuplicateKeyError 语义与
生产 DB 唯一约束（同用户同 provider 仅一条）对齐。
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


class DuplicateKeyError(ValueError):
    """同一用户同一 provider 重复创建。"""


class ApiKeyStore(Protocol):
    """用户 API Key 仓储协议（生命周期 + 计量）。"""

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        provider: str,
        encrypted_key: str,
        monthly_budget: int | None,
    ) -> Any: ...
    async def list_for_user(self, user_id: uuid.UUID) -> list[Any]: ...
    async def delete(self, user_id: uuid.UUID, key_id: uuid.UUID) -> bool: ...
    async def get_active(self, user_id: uuid.UUID, provider: str) -> Any | None: ...
    async def add_used_budget(self, key_id: uuid.UUID, cents: int) -> int: ...


class SqlAlchemyApiKeyStore:
    """生产实现（PostgreSQL）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **kwargs) -> UserApiKey:
        record = UserApiKey(**kwargs)
        self.session.add(record)
        await self.session.commit()
        return record

    async def list_for_user(self, user_id: uuid.UUID) -> list[UserApiKey]:
        result = await self.session.execute(
            select(UserApiKey)
            .where(UserApiKey.user_id == user_id)
            .order_by(UserApiKey.created_at.desc())
        )
        return list(result.scalars())

    async def delete(self, user_id: uuid.UUID, key_id: uuid.UUID) -> bool:
        record = await self.session.get(UserApiKey, key_id)
        if record is None or record.user_id != user_id:
            return False
        await self.session.delete(record)
        await self.session.commit()
        return True

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
    """测试用内存实现（本地替代品）。"""

    def __init__(self) -> None:
        self._records: dict[uuid.UUID, ApiKeyRecord] = {}

    async def create(self, **kwargs) -> ApiKeyRecord:
        if any(
            r.user_id == kwargs["user_id"] and r.provider == kwargs["provider"]
            for r in self._records.values()
        ):
            raise DuplicateKeyError("同一服务商仅可绑定一个 Key")
        record = ApiKeyRecord(**kwargs)
        self._records[record.id] = record
        return record

    async def list_for_user(self, user_id: uuid.UUID) -> list[ApiKeyRecord]:
        return [r for r in self._records.values() if r.user_id == user_id]

    async def delete(self, user_id: uuid.UUID, key_id: uuid.UUID) -> bool:
        record = self._records.get(key_id)
        if record is None or record.user_id != user_id:
            return False
        del self._records[key_id]
        return True

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
