# BFF Dockerfile（docker-compose 中 build context 为 ./backend）
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# 先安装本地公共库，再装本服务依赖（充分利用镜像层缓存）
COPY shared/ ./shared/
RUN pip install --no-cache-dir -e ./shared

COPY bff/ ./bff/
RUN pip install --no-cache-dir -r ./bff/requirements.txt

WORKDIR /app/bff
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
