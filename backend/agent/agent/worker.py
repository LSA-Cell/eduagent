"""compose 兼容 worker 入口：`celery -A agent.worker worker --loglevel=INFO --concurrency=4`。

等价于 `celery -A app.tasks.worker`；任务与 Celery 应用定义见 app/tasks/。
"""

from __future__ import annotations

from app.tasks.worker import celery_app  # noqa: F401 - 导入即注册全部任务

# celery -A <module> 按 app / celery 属性名发现应用实例
app = celery_app
celery = celery_app

__all__ = ["celery_app", "app", "celery"]
