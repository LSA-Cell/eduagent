# 多模态服务（ASR/OCR/TTS/文档解析/图片预处理）
# 构建上下文为 backend/：docker build -f multimodal/Dockerfile backend/
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先安装共享包（pip 本地包 eduagent_shared）
COPY shared/ /app/shared/
RUN pip install -e /app/shared

# 再安装本服务依赖与代码
COPY multimodal/requirements.txt /app/multimodal/requirements.txt
RUN pip install -r /app/multimodal/requirements.txt
COPY multimodal/ /app/multimodal/

WORKDIR /app/multimodal
EXPOSE 8003
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
