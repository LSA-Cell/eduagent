"""agent 服务测试公共 fixtures：全部离线（假 DB 会话 / 脚本化 LLM / Celery eager）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence

import pytest

from app.memory.embeddings import hash_embed
from app.services.llm_client import ChatResult
from app.tasks.celery_app import celery_app

# Celery eager 模式：.delay() 同步执行，不需要真实 broker
celery_app.conf.update(task_always_eager=True, task_eager_propagates=False)

TEST_USER_ID = str(uuid.uuid4())
INTERNAL_TOKEN = "test-internal-token"


def auth_headers(user_id: Optional[str] = None, token: str = INTERNAL_TOKEN) -> dict[str, str]:
    """BFF 转发约定请求头。"""
    return {"X-Internal-Token": token, "X-User-Id": user_id or TEST_USER_ID}


class FakeResult:
    """sqlalchemy Result 鸭子类型（scalars()/all()）。"""

    def __init__(self, rows: Sequence[Any] = ()) -> None:
        self._rows = list(rows)

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> "FakeResult":
        return self


def _stmt_entity(stmt: Any) -> Optional[type]:
    """从 select(Model) 语句取实体类型；text() 等返回 None。"""
    descriptions = getattr(stmt, "column_descriptions", None)
    if descriptions:
        return descriptions[0].get("entity")
    return None


class FakeSession:
    """异步 DB 会话鸭子类型。

    - get/add/flush/delete：内存对象表（flush 时补 id 与 created_at）；
    - execute(select(Model))：按实体类型返回已登记对象（忽略过滤/排序，测试自证）；
    - execute(text(...))：委托 sql_handler(stmt, params) 钩子，默认空结果。
    """

    def __init__(self, sql_handler: Optional[Callable[[Any, Any], Optional[list[Any]]]] = None) -> None:
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.commits = 0
        self.rollbacks = 0
        self.sql_handler = sql_handler
        self._by_id: dict[tuple[type, str], Any] = {}

    async def get(self, model: type, ident: Any) -> Any:
        return self._by_id.get((model, str(ident)))

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            if getattr(obj, "created_at", None) is None:
                obj.created_at = datetime.now(timezone.utc)
            self._by_id[(type(obj), str(obj.id))] = obj

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        if self.sql_handler is not None:
            handled = self.sql_handler(stmt, params)
            if handled is not None:
                return FakeResult(handled)
        entity = _stmt_entity(stmt)
        if entity is not None:
            return FakeResult([o for o in self._by_id.values() if isinstance(o, entity)])
        return FakeResult([])

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)
        self._by_id.pop((type(obj), str(getattr(obj, "id", ""))), None)

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def as_factory(session: FakeSession) -> Callable[[], FakeSession]:
    """把 FakeSession 包装成 session_factory（async with factory() 语义）。"""
    return lambda: session


class FakeLLM:
    """脚本化 LLM 替身（LLMClient 同构接口）。

    - chat：依次弹出 chat_results（元素为异常实例则抛出），用完后返回默认文本；
    - chat_stream：按 stream_chunks 逐段产出；
    - chat_json：固定返回 json_result（若是异常实例则抛出）；
    - embed：本地哈希伪向量。
    """

    def __init__(
        self,
        *,
        chat_results: Optional[Sequence[Any]] = None,
        stream_chunks: Sequence[str] = (),
        json_result: Any = None,
        default_content: str = "（默认回复）",
    ) -> None:
        self.chat_results = list(chat_results or [])
        self.stream_chunks = list(stream_chunks)
        self.json_result = json_result
        self.default_content = default_content
        self.chat_calls: list[dict[str, Any]] = []
        self.json_calls: list[dict[str, Any]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> ChatResult:
        self.chat_calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        if self.chat_results:
            result = self.chat_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return ChatResult(content=self.default_content)

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
    ):
        for chunk in self.stream_chunks:
            yield chunk

    async def chat_json(
        self, system: str, user: str, *, model: Optional[str] = None, temperature: float = 0.3
    ) -> Any:
        self.json_calls.append({"system": system, "user": user})
        if isinstance(self.json_result, Exception):
            raise self.json_result
        return self.json_result

    async def embed(self, texts: list[str], *, model: Optional[str] = None) -> list[list[float]]:
        return [hash_embed(t) for t in texts]


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()
