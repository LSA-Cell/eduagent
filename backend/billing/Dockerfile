# Billing 服务 Dockerfile（docker-compose 中 build context 为 ./backend）
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# 先安装本地公共库，再装本服务依赖（充分利用镜像层缓存）
COPY shared/ ./shared/
RUN pip install --no-cache-dir -e ./shared

COPY billing/ ./billing/
RUN pip install --no-cache-dir -r ./billing/requirements.txt

WORKDIR /app/billing
EXPOSE 8006
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8006"]
