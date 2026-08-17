# 推理网关 Dockerfile（docker-compose 中 build context 为 ./backend）
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY shared/ ./shared/
RUN pip install --no-cache-dir -e ./shared

COPY gateway/ ./gateway/
RUN pip install --no-cache-dir -r ./gateway/requirements.txt

WORKDIR /app/gateway
EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
