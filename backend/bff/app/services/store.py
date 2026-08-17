"""仓储层：DB 访问抽象。

- 协议（Protocol）定义接口，便于测试用内存实现替换（dependency override），
  避免测试依赖真实 PostgreSQL（模型含 UUID/JSONB/Vector 等 PG 专属类型，无法跑 SQLite）。
- SQLAlchemy* 为生产实现；InMemory* 为测试实现，属性名与 ORM 模型保持一致，
  使 Pydantic 的 from_attributes 序列化对两者通用。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from eduagent_shared.models import ChatSession, Message, User


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 用户
# ---------------------------------------------------------------------------


class UserStore(Protocol):
    async def get_by_phone(self, phone: str) -> Any | None: ...
    async def get_by_id(self, user_id: uuid.UUID) -> Any | None: ...
    async def create(
        self,
        *,
        phone: str,
        password_hash: str,
        email: str | None = None,
        grade_level: str | None = None,
        default_subject: str | None = None,
    ) -> Any: ...
    async def set_mode(self, user_id: uuid.UUID, mode: str) -> Any | None: ...
    async def update_profile(
        self,
        user_id: uuid.UUID,
        *,
        grade_level: str | None = None,
        default_subject: str | None = None,
    ) -> Any | None: ...


class SqlAlchemyUserStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_phone(self, phone: str) -> User | None:
        result = await self.session.execute(select(User).where(User.phone == phone))
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def create(self, **kwargs) -> User:
        user = User(**kwargs)
        self.session.add(user)
        await self.session.commit()
        return user

    async def set_mode(self, user_id: uuid.UUID, mode: str) -> User | None:
        user = await self.session.get(User, user_id)
        if user is None:
            return None
        user.user_mode = mode
        await self.session.commit()
        return user

    async def update_profile(
        self,
        user_id: uuid.UUID,
        *,
        grade_level: str | None = None,
        default_subject: str | None = None,
    ) -> User | None:
        """仅更新非 None 字段（None 语义为"保持原值"）。"""
        user = await self.session.get(User, user_id)
        if user is None:
            return None
        if grade_level is not None:
            user.grade_level = grade_level
        if default_subject is not None:
            user.default_subject = default_subject
        await self.session.commit()
        return user


@dataclass
class UserRecord:
    phone: str
    password_hash: str
    email: str | None = None
    grade_level: str | None = None
    default_subject: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    is_teacher: bool = False
    is_paid: bool = False
    user_mode: str = "student"
    teacher_verified_at: datetime | None = None
    created_at: datetime = field(default_factory=_utcnow)


class InMemoryUserStore:
    """测试用内存实现。"""

    def __init__(self) -> None:
        self._by_id: dict[uuid.UUID, UserRecord] = {}

    async def get_by_phone(self, phone: str) -> UserRecord | None:
        return next((u for u in self._by_id.values() if u.phone == phone), None)

    async def get_by_id(self, user_id: uuid.UUID) -> UserRecord | None:
        return self._by_id.get(user_id)

    async def create(self, **kwargs) -> UserRecord:
        record = UserRecord(**kwargs)
        self._by_id[record.id] = record
        return record

    async def set_mode(self, user_id: uuid.UUID, mode: str) -> UserRecord | None:
        record = self._by_id.get(user_id)
        if record is not None:
            record.user_mode = mode
        return record

    async def update_profile(
        self,
        user_id: uuid.UUID,
        *,
        grade_level: str | None = None,
        default_subject: str | None = None,
    ) -> UserRecord | None:
        record = self._by_id.get(user_id)
        if record is None:
            return None
        if grade_level is not None:
            record.grade_level = grade_level
        if default_subject is not None:
            record.default_subject = default_subject
        return record


# ---------------------------------------------------------------------------
# 用户 API Key
# ---------------------------------------------------------------------------
# 唯一实现已收敛到 eduagent_shared.stores.api_keys（BFF/Proxy 共用，见该模块
# 红线说明）；此处再导出仅为保持本服务既有 import 路径不变。

from eduagent_shared.stores.api_keys import (  # noqa: E402,F401
    ApiKeyRecord,
    ApiKeyStore,
    DuplicateKeyError,
    InMemoryApiKeyStore,
    SqlAlchemyApiKeyStore,
)


# ---------------------------------------------------------------------------
# 聊天会话与消息
# ---------------------------------------------------------------------------


class ChatStore(Protocol):
    async def create_session(self, *, user_id: uuid.UUID, **kwargs) -> Any: ...
    async def get_session(self, session_id: uuid.UUID) -> Any | None: ...
    async def list_sessions(
        self, user_id: uuid.UUID, page: int, page_size: int
    ) -> tuple[list[Any], int]: ...
    async def add_message(self, *, session_id: uuid.UUID, **kwargs) -> Any: ...
    async def list_messages(
        self, session_id: uuid.UUID, page: int, page_size: int
    ) -> tuple[list[Any], int]: ...


class SqlAlchemyChatStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_session(self, *, user_id: uuid.UUID, **kwargs) -> ChatSession:
        session = ChatSession(user_id=user_id, **kwargs)
        self.session.add(session)
        await self.session.commit()
        return session

    async def get_session(self, session_id: uuid.UUID) -> ChatSession | None:
        return await self.session.get(ChatSession, session_id)

    async def list_sessions(
        self, user_id: uuid.UUID, page: int, page_size: int
    ) -> tuple[list[ChatSession], int]:
        base = select(ChatSession).where(ChatSession.user_id == user_id)
        total = await self.session.scalar(
            select(func.count()).select_from(base.subquery())
        )
        result = await self.session.execute(
            base.order_by(ChatSession.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars()), int(total or 0)

    async def add_message(self, *, session_id: uuid.UUID, **kwargs) -> Message:
        message = Message(session_id=session_id, **kwargs)
        self.session.add(message)
        # 更新会话 updated_at
        chat_session = await self.session.get(ChatSession, session_id)
        if chat_session is not None:
            chat_session.updated_at = _utcnow()
        await self.session.commit()
        return message

    async def list_messages(
        self, session_id: uuid.UUID, page: int, page_size: int
    ) -> tuple[list[Message], int]:
        base = select(Message).where(Message.session_id == session_id)
        total = await self.session.scalar(
            select(func.count()).select_from(base.subquery())
        )
        result = await self.session.execute(
            base.order_by(Message.created_at.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars()), int(total or 0)


@dataclass
class SessionRecord:
    user_id: uuid.UUID
    title: str | None = None
    subject: str | None = None
    model: str | None = None
    inference_source: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


@dataclass
class MessageRecord:
    session_id: uuid.UUID
    role: str
    content: str
    content_type: str = "text"
    parent_id: uuid.UUID | None = None
    metadata_: dict = field(default_factory=dict)
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=_utcnow)


class InMemoryChatStore:
    def __init__(self) -> None:
        self._sessions: dict[uuid.UUID, SessionRecord] = {}
        self._messages: dict[uuid.UUID, MessageRecord] = {}

    async def create_session(self, *, user_id: uuid.UUID, **kwargs) -> SessionRecord:
        record = SessionRecord(user_id=user_id, **kwargs)
        self._sessions[record.id] = record
        return record

    async def get_session(self, session_id: uuid.UUID) -> SessionRecord | None:
        return self._sessions.get(session_id)

    async def list_sessions(
        self, user_id: uuid.UUID, page: int, page_size: int
    ) -> tuple[list[SessionRecord], int]:
        items = sorted(
            (s for s in self._sessions.values() if s.user_id == user_id),
            key=lambda s: s.updated_at,
            reverse=True,
        )
        start = (page - 1) * page_size
        return items[start : start + page_size], len(items)

    async def add_message(self, *, session_id: uuid.UUID, **kwargs) -> MessageRecord:
        record = MessageRecord(session_id=session_id, **kwargs)
        self._messages[record.id] = record
        session = self._sessions.get(session_id)
        if session is not None:
            session.updated_at = _utcnow()
        return record

    async def list_messages(
        self, session_id: uuid.UUID, page: int, page_size: int
    ) -> tuple[list[MessageRecord], int]:
        items = sorted(
            (m for m in self._messages.values() if m.session_id == session_id),
            key=lambda m: m.created_at,
        )
        start = (page - 1) * page_size
        return items[start : start + page_size], len(items)
