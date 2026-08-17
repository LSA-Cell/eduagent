# EduAgent Agent 编排服务
# 构建上下文为 backend/ 目录：
#   docker build -f agent/Dockerfile -t eduagent-agent ./backend
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 先装公共包（同事并行开发的 eduagent_shared）
COPY shared/ ./shared/
RUN pip install --no-cache-dir ./shared

# 再装本服务依赖（利用镜像层缓存）
COPY agent/requirements.txt ./agent/requirements.txt
RUN pip install --no-cache-dir -r agent/requirements.txt

COPY agent/ ./agent/

WORKDIR /app/agent
EXPOSE 8002

# API 服务启动命令；Celery worker 在 compose 中用以下命令覆盖启动：
#   celery -A app.tasks.celery_app:celery_app worker --loglevel=INFO -Q agent
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
