"""Celery 异步任务包。

- celery_app：broker/backend 取自全局 settings；
- mistake_tasks / memory_tasks：聊天后处理（错题提取 / 记忆提取）；
- gen_tasks：教师内容生成与学习计划排程（LLM + docgen 渲染）。

worker 启动见 app/tasks/worker.py docstring。
"""
