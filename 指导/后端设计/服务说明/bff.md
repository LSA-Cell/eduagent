# EduAgent BFF（端口 8000）

面向移动端的后端聚合层：鉴权、限流、会话/消息 REST、聊天转发（SSE 透传到推理网关）、
业务路由转发（Agent 编排服务）、用户 API Key 管理、身份切换、WebSocket 占位。

## 本地启动

```bash
# 在 backend/ 目录下
pip install -e shared -r bff/requirements.txt
cd bff && uvicorn app.main:app --reload --port 8000
```

## 运行测试（无需 PostgreSQL / Redis，依赖全部 mock / fakeredis）

```bash
# 在 backend/ 目录下
pip install -e shared -r bff/requirements-dev.txt
cd bff && python -m pytest
```

> 说明：本服务未做成 pip 包，测试通过 `bff/conftest.py` 把 `bff/` 与 `shared/`
> 加入 `sys.path` 后直接 `import app`；若已 `pip install -e shared` 则共用环境安装。

## Docker

由根目录 docker-compose 构建（build context 为 `./backend`，dockerfile 为 `bff/Dockerfile`）。

## 主要接口

- `POST /auth/register` `POST /auth/login` `POST /auth/refresh`
- `GET /users/me` `PUT /users/me/mode`（student / teacher / parent）
- `POST|GET|DELETE /users/me/api-keys`（密文存储，响应仅含掩码后 4 位）
- `POST|GET /chat/sessions`、`GET /chat/sessions/{id}/messages`（分页）
- `POST /chat/completions` → 转发推理网关，SSE 透传
- 业务转发：`/mistakes`、`/flashcards`、`/study-plans`、`/kb/documents`（含 `POST /kb/documents/upload-url`）、
  `/teacher/lesson-plans|presentations|exam-papers|learning-analyses` → Agent 编排服务
- `WS /ws/chat?token=<access_token>`：echo / ping / chat（非流式转发网关）
- `GET /healthz`
