"""Celery worker 入口模块。

启动命令（工作目录 backend/agent）：
    celery -A app.tasks.worker worker --loglevel=INFO --concurrency=4

导入本模块即完成全部任务注册；compose 中使用的 `celery -A agent.worker`
由顶层 agent/ 兼容包转发到这里。
"""

from __future__ import annotations

from app.tasks import gen_tasks, kb_tasks, memory_tasks, mistake_tasks, plan_tasks  # noqa: F401 - 注册任务
from app.dream import tasks as dream_tasks  # noqa: F401 - Wave D2：DreamTask 注册
from app.hooks import tasks as hook_tasks  # noqa: F401 - Wave D2：自动化 Hook 注册
from app.tasks.celery_app import celery_app

# celery -A <module> 按 app / celery 属性名发现应用实例
app = celery_app
celery = celery_app

__all__ = ["celery_app", "app", "celery", "gen_tasks", "kb_tasks", "memory_tasks",
           "mistake_tasks", "plan_tasks", "dream_tasks", "hook_tasks"]
