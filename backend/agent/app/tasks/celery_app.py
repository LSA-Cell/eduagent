"""Celery 应用工厂：broker / result backend 取自全局 settings。

worker 启动（工作目录 backend/agent，二选一）：
    celery -A app.tasks.worker worker --loglevel=INFO --concurrency=4
    celery -A agent.worker      worker --loglevel=INFO --concurrency=4  # compose 兼容入口
"""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine

from celery import Celery

from eduagent_shared import db
from eduagent_shared.config import get_settings

_TASK_MODULES = [
    "app.tasks.mistake_tasks",
    "app.tasks.memory_tasks",
    "app.tasks.gen_tasks",
    "app.tasks.kb_tasks",
    "app.tasks.subagent_tasks",
    "app.tasks.plan_tasks",
    "app.dream.tasks",  # Wave D2：DreamTask 夜间记忆整合（§4.2）
    "app.hooks.tasks",  # Wave D2：自动化 Hook 动作执行（§5.4）
]


def create_celery() -> Celery:
    """按 settings 构造 Celery 应用（创建对象不建立连接，离线可导入）。"""
    from celery.schedules import crontab

    settings = get_settings()
    app = Celery(
        "agent",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        include=_TASK_MODULES,
    )
    app.conf.update(
        task_default_queue="agent",
        task_track_started=True,
        task_time_limit=900,  # 内容生成含 LLM + 渲染轮询，放宽到 15 分钟
        task_soft_time_limit=840,
        result_expires=3600,
        broker_connection_retry_on_startup=True,
        worker_prefetch_multiplier=1,  # 长任务逐个领取，避免堆积在单 worker
        # Wave D2（§4.2）：DreamTask 走低优先级独立队列，禁止挤占白天服务资源
        task_routes={"agent.dream.run_user": {"queue": "dream"}},
        # Wave D2（§4.2）：每日 02:00（ Asia/Shanghai ）起分批派发夜间整合
        timezone="Asia/Shanghai",
        beat_schedule={
            "dream-nightly-dispatch": {
                "task": "agent.dream.dispatch",
                "schedule": crontab(hour=2, minute=0),
            },
        },
    )
    return app


celery_app = create_celery()


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """在同步任务中执行异步实现。

    每个任务独立事件循环，结束后释放共享 DB 引擎（asyncpg 连接绑定创建它的
    loop，跨任务复用会报 "attached to a different loop"）。
    """

    async def _runner() -> Any:
        try:
            return await coro
        finally:
            await db.dispose_engine()

    return asyncio.run(_runner())
