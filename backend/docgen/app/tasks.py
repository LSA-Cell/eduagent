"""任务表：异步渲染任务的状态跟踪（内存 / Redis 两种后端）。

边界说明：本服务不访问业务数据库，任务状态仅存内存或 Redis。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

TASK_TTL_SECONDS = 24 * 3600


@dataclass
class TaskRecord:
    """渲染任务记录。"""

    task_id: str
    task_type: str
    status: str = "pending"  # pending / processing / completed / failed
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskStore(Protocol):
    """任务存储接口。"""

    def create(self, task_type: str) -> TaskRecord: ...
    def update(self, record: TaskRecord) -> None: ...
    def get(self, task_id: str) -> TaskRecord | None: ...


class MemoryTaskStore:
    """进程内存任务表（默认，单实例部署适用）。"""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}

    def create(self, task_type: str) -> TaskRecord:
        record = TaskRecord(task_id=uuid.uuid4().hex, task_type=task_type)
        self._tasks[record.task_id] = record
        return record

    def update(self, record: TaskRecord) -> None:
        self._tasks[record.task_id] = record

    def get(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)


class RedisTaskStore:
    """Redis 任务表（多实例部署适用），key 带 TTL。"""

    PREFIX = "docgen:task:"

    def __init__(self, redis_url: str) -> None:
        import redis

        self._client = redis.Redis.from_url(redis_url, decode_responses=True)

    def ping(self) -> None:
        """探测连通性，失败抛 redis.RedisError。"""
        self._client.ping()

    def create(self, task_type: str) -> TaskRecord:
        record = TaskRecord(task_id=uuid.uuid4().hex, task_type=task_type)
        self.update(record)
        return record

    def update(self, record: TaskRecord) -> None:
        self._client.setex(
            self.PREFIX + record.task_id, TASK_TTL_SECONDS, json.dumps(record.to_dict())
        )

    def get(self, task_id: str) -> TaskRecord | None:
        raw = self._client.get(self.PREFIX + task_id)
        if raw is None:
            return None
        return TaskRecord(**json.loads(raw))


_task_store: TaskStore | None = None


def get_task_store() -> TaskStore:
    """按配置返回任务存储：配置了 REDIS_URL 且 redis 可用时用 Redis，否则内存。"""
    global _task_store
    if _task_store is not None:
        return _task_store

    from app.config import get_settings

    redis_url = get_settings().redis_url
    if redis_url:
        try:
            store = RedisTaskStore(redis_url)
            store.ping()  # 确认连通性，不可达则回退内存表
            _task_store = store
            return _task_store
        except Exception:  # noqa: BLE001 - redis 不可用时回退内存表
            pass
    _task_store = MemoryTaskStore()
    return _task_store


def reset_task_store_for_tests() -> None:
    """测试辅助：重置任务存储单例。"""
    global _task_store
    _task_store = None
