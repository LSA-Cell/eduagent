# 文档生成服务（PPTX/DOCX/PDF 排版渲染 + MinIO 上传）
# 构建上下文为 backend/：docker build -f docgen/Dockerfile backend/
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先安装共享包（pip 本地包 eduagent_shared）
COPY shared/ /app/shared/
RUN pip install -e /app/shared

# 再安装本服务依赖与代码
COPY docgen/requirements.txt /app/docgen/requirements.txt
RUN pip install -r /app/docgen/requirements.txt
COPY docgen/ /app/docgen/

WORKDIR /app/docgen
EXPOSE 8005
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8005"]
