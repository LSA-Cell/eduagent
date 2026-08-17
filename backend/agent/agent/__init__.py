"""compose 兼容包：使 `celery -A agent.worker` 可导入。

真实实现位于 app/tasks/（包结构为 app/）；本包只做转发，不承载逻辑。
容器内工作目录为 /app/agent，cwd 在 sys.path 上，故此包可被 celery 发现。
"""
