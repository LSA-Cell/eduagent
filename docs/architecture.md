# EduAgent 架构文档

面向 K12 至大学阶段的教育 AI Agent 移动应用。Mono-repo：Flutter 移动端 + FastAPI 微服务后端 + vLLM 自有推理 + PostgreSQL/pgvector + Redis + MinIO，Docker Compose 编排，Nginx 反向代理。

---

## 1. 总体架构

```
                            ┌────────────────────────────────────────────┐
                            │                移动端 (Flutter)             │
                            │   Riverpod / Dio / SSE / WebSocket / Hive   │
                            └───────────────────┬────────────────────────┘
                                                │ HTTPS（REST / SSE / WS）
                                                ▼
┌─────────────────────────────────────────────── net_edge ───────────────────────────────┐
│                                 Nginx (:80/:443)                                        │
│          SSL 终止 · 限流 · /api → bff · /api/v1/chat/completions 与 /chat/stream SSE · /ws   │
└───────────────────────────────────────┬────────────────────────────────────────────────┘
                                        │
                                        ▼
┌──────────────────────────────────────── net_app ────────────────────────────────────────┐
│  ┌───────────┐   HTTP/SSE   ┌──────────────────┐                                        │
│  │   BFF     │─────────────▶│  Gateway 推理网关 │────────┐                               │
│  │  :8000    │              │  :8001 双轨路由   │        │                               │
│  │ 鉴权/校验/ │              └──────────────────┘        │                               │
│  │ 限流/路由  │──┬──────────▶ Agent :8002（编排：记忆/RAG/│                               │
│  └─────┬─────┘  │            工具/生成引擎）+ Celery      │                               │
│        │        ├──────────▶ Multimodal :8003            │                               │
│        │        │            (ASR/OCR/TTS/文档解析)       │                               │
│        │        ├──────────▶ Proxy :8004（用户 Key 代理） │                               │
│        │        └──────────▶ Docgen :8005（排版生成）     │                               │
└────────┼────────────────┼───────────────────────────────┼───────────────────────────────┘
         │                │                               │
         ▼                ▼                               ▼
┌──────────────── net_data (internal) ────────┐   ┌────── net_inference ──────────────────┐
│  PostgreSQL 15 + pgvector :5432             │   │  vLLM-general :8100 (edu-general-14b) │
│  Redis 7 :6379（缓存 + Celery Broker）       │   │  vLLM-math    :8101 (edu-math-7b)     │
│  MinIO :9000/:9001（私有桶 + 预签名 URL）     │   │  （gpu profile，OpenAI 兼容，无状态）  │
└─────────────────────────────────────────────┘   │  Proxy :8004（外呼第三方 LLM 的出口）  │
                                                  └───────────────────────────────────────┘

网络隔离红线：
  · net_edge 只有 nginx 与 bff —— postgres/redis/minio 对边缘完全不可达
  · vLLM 只在 net_inference —— 只有 gateway（及推理出口 proxy）能访问
  · net_data 为 internal 网络 —— 数据组件无出站能力
```

---

## 2. 服务职责与边界红线

| 服务 | 端口 | 职责 | 边界红线 |
|------|------|------|----------|
| Nginx | 80/443 | SSL 终止、限流（按 IP）、SSE/WS 透传、静态资源 | 只做转发与边缘防护，无任何业务逻辑 |
| BFF | 8000 | 鉴权（JWT）、参数校验、按用户限流、聚合编排、路由转发 | **禁止直连 GPU/vLLM 进程**；聊天一律经 Agent 编排（BFF 不直接调 Gateway 做聊天、不写 messages，只转发/翻译事件流）；移动端**禁止直连 DB/推理层**（由网络隔离强制） |
| Gateway（推理网关） | 8001 | 双轨路由（自有 vLLM / 用户 Key→Proxy）、学科→模型路由、SSE 中继、健康探测、故障降级 | **禁止业务逻辑**：不写业务表、不做记忆/RAG/工具编排，只做「模型在哪、通不通、转给谁」 |
| Agent（编排） | 8002 | 记忆读写、RAG 检索、工具系统、内容生成引擎、任务编排 | **禁止同步执行长任务**：PPT/教案/试卷等一律投 Celery，HTTP 立即返回 202 |
| Agent Worker | —（同 :8002 镜像） | Celery 消费：内容生成、错题提取、复习调度、embedding | 无 HTTP 入口；只经 Broker 取任务 |
| Multimodal | 8003 | ASR、OCR、TTS、文档解析、图片预处理 | **无学科业务**：不判断对错、不做讲解，输出纯结构化结果（文本/坐标/时长） |
| Proxy（API 代理） | 8004 | 用户自带 Key 解密、转发第三方 LLM、额度计量 | **禁止泄露 Key**：密文只在内存中瞬时使用，不落盘、不进日志、不出现在响应体 |
| Docgen（文档生成） | 8005 | python-pptx / python-docx / PDF 排版，产物写 MinIO | **只排版不生成内容**：内容 JSON 必须来自 Agent，Docgen 不调用任何 LLM |
| vLLM ×2 | 8100/8101 | 自有模型推理（OpenAI 兼容 `/v1/chat/completions`） | **无状态纯推理**：不访问 DB/Redis/MinIO，不保存会话，可被随时替换/扩容 |
| PostgreSQL + pgvector | 5432 | 业务数据 + 向量检索（kb_chunks / memories），RLS 行级隔离 | 只在 net_data；业务连接必须设置 `app.current_user_id` |
| Redis | 6379 | 缓存、限流计数、Celery Broker/Result Backend（db 0/1/2） | 不存持久业务数据；Key 密文禁止入 Redis |
| MinIO | 9000/9001 | 私有桶 ×3（uploads/generated/templates），预签名 URL 直传直取 | 桶一律私有；URL 有效期 ≤ 1h |

---

## 3. 数据流

### 3.1 聊天 SSE 流式链路

```
移动端 ──POST /api/v1/chat/completions (SSE)──▶ Nginx ─▶ BFF
  BFF：JWT 校验 → 用户级限流 → 会话归属校验(404) → 转发 Agent POST /v1/chat/stream
       （注入 role/grade_level/quota_tier 上下文；不再写 messages，消除双落库）
  Agent：读记忆(memories) + RAG(kb_chunks 向量召回) + 护栏链 + 组装 Prompt
       ─▶ Gateway：按 subject 选模型；inference_source=internal → vLLM
                                        └ external → Proxy（用户 Key）
  vLLM/Proxy：逐 token 产生 ─▶ Gateway ─▶ Agent（9 事件协议：run.start/message.delta/
    tool.*/card/plan.*/error/run.end；统一落库 user+assistant 消息与 token 用量）
    ─▶ BFF 翻译层：Agent 事件流转译为 OpenAI chat.completion.chunk（增量 content /
      finish_reason / [DONE]）─▶ Nginx(proxy_buffering off) ─▶ 移动端逐 token 渲染
  流结束后：Proxy 侧扣 used_budget
```

- `POST /api/v1/chat/stream`：BFF 原样透传 Agent 9 事件流（不翻译），供已升级新协议的
  客户端消费卡片/工具/计划等富事件；支持 `Last-Event-ID` 请求头或 `?run_id=` 断线重放。
- 消息持久化由 Agent 单侧负责（`_persist_turn`），BFF 只做转发与协议翻译。
- 要点：Nginx 对两个 location 均关闭 `proxy_buffering`，`read_timeout 600s`；任一环故障时按 §4 降级。

### 3.2 拍照答疑链路

```
移动端拍照 ─▶ 向 BFF 申请预签名 PUT URL ─▶ 直传 MinIO eduagent-uploads（不过服务器）
       ─▶ POST /api/v1/chat/completions（content_type=image，带对象 key）
BFF ─▶ Agent ─▶ Multimodal：OCR + 图片预处理 → 题目文本/公式结构化
Agent：题目理解 → Gateway(internal, subject=math 路由 edu-math-7b) → 讲解/答案流式返回
异步：Agent Worker 提取错题候选 → mistake_records（knowledge_points、next_review_at=now+1d）
```

### 3.3 PPT / 教案 / 试卷异步生成链路

```
移动端 POST /api/v1/generate/presentations ─▶ BFF ─▶ Agent
  Agent：校验参数 → 建 presentations 记录(status=pending) → 投 Celery ─▶ 返回 202 + task_id
Agent Worker：
  1) 经 Gateway 生成结构化内容 JSON（slides/questions/content）
  2) 调 Docgen 排版 → pptx/docx/pdf 写入 MinIO eduagent-generated
  3) 更新记录 status=completed、file_url、slides/questions 预览结构
移动端：GET /api/v1/generate/tasks/{task_id} 轮询（或 WS 推送）→ 完成后换取 ≤1h 预签名 GET URL
```

红线对应：Agent 不阻塞（202 立即返回）；Docgen 只排版；LLM 只在 Gateway 之后。

### 3.4 错题提取与艾宾浩斯复习链路

```
触发（两路）：
  a) 聊天中判错/用户标记「加入错题本」
  b) Worker 定期扫描含工具判定结果的消息
Worker：生成分析 + knowledge_points → mistake_records（review_count=0, next_review_at=+1 天）
复习调度（Worker 定时任务）：
  每日扫描 next_review_at <= now() 的记录 → 推送/出现在「今日复习」
  用户复习后按艾宾浩斯间隔序列回写：1d → 2d → 4d → 7d → 15d → 30d，review_count+1
记忆卡片（flashcards）走 SM-2：按回忆质量更新 interval_days / ease_factor / next_review_at
```

---

## 4. 降级策略

| 故障点 | 检测 | 降级动作 |
|--------|------|----------|
| 单个 vLLM 实例不健康 | Gateway 主动健康探测（TTL 30s，见 `MODEL_HEALTH_TTL_SECONDS`） | 路由表内剔除该实例，同模型其余实例接管 |
| 某学科模型全部不可用 | 探测 + 请求失败计数 | `FAILOVER_ENABLED=true` 时回退通用模型 edu-general-14b，响应头标记 `X-Fallback: true` |
| 自有推理整体不可用 | 连续失败阈值 | 用户已配置外部 Key → 切 external 轨；否则返回 503 + 明确错误码，**绝不静默吞错** |
| 用户外部 Key 失效/超额 | Proxy 捕获上游 401/429、月度预算校验 | 回退 internal 轨并提示用户「已切换到内置模型/请检查 Key 与额度」 |
| Redis 不可用 | 连接失败 | 限流退化为进程内计数（放松为兜底阈值）；Celery 任务暂停接收并告警 |
| Multimodal 不可用 | 健康检查 | 图片/语音题暂收文本模式，提示用户「拍照识别暂不可用，可手动输入题目」 |
| Docgen / MinIO 写失败 | 任务异常 | Celery 指数退避重试（最多 3 次）→ 仍失败则记录 status=failed，移动端可手动重试 |
| BFF 单点 | — | `--scale bff=N` 多副本 + Nginx least_conn |

---

## 5. 安全设计

- **JWT 双令牌**：Access Token 15 分钟（`ACCESS_TOKEN_EXPIRE_MINUTES=15`），Refresh Token 7 天（`REFRESH_TOKEN_EXPIRE_DAYS=7`），HS256；刷新接口做旋转（旧 refresh 作废）。所有受保护接口仅接受 `Authorization: Bearer`。
- **用户 API Key 管理（AES-256-GCM）**：用户自带 Key 在 BFF 即加密后落库（`user_api_keys.encrypted_key`），主密钥为 32 字节 base64（`KEY_ENCRYPTION_MASTER_KEY`），轮换周期 90 天（密文带密钥版本前缀，支持双密钥滚动解密）。**任何接口永不回显明文 Key**；Proxy 仅在转发瞬间于内存解密，不写日志、不落盘。
- **行级安全（RLS）**：所有含 `user_id` 的表启用 `ENABLE + FORCE ROW LEVEL SECURITY`，策略基于 `app.current_user_id` 会话变量；`messages` 经 `chat_sessions`、`kb_chunks` 经 `knowledge_documents` 判定归属。未设置身份 = 默认拒绝。服务/管理角色需显式 `BYPASSRLS` 并仅限内网受信用途（见 `infra/postgres/init.sql` 第 8 节注释）。
- **预签名 URL ≤ 1h**：上传（PUT）与下载（GET）URL 均由 BFF/Agent 签发，有效期不超过 1 小时；三个桶全部私有（`mc anonymous set none`）；移动端直传 MinIO，文件不经过应用服务器。
- **服务间认证**：内部调用携带 `INTERNAL_SERVICE_TOKEN`，各服务统一经 `eduagent_shared.security.verify_internal_token` 校验（docgen/multimodal 的"未配置令牌则开发放行"本地降级已移除）；集成冒烟脚本不再内置默认令牌，缺令牌即失败退出。数据层网络 `internal: true` 无出站；vLLM 仅 `net_inference` 可达。
- **双层限流**：Nginx 按 IP 粗粒度（API 30 r/s、鉴权 5 r/s），BFF 按用户细粒度（`RATE_LIMIT_FREE/PAID/TEACHER` = 20/60/100 RPM）。

---

## 6. 部署与扩展

### 6.1 启动

```bash
cp .env.example .env          # 修改所有 change_me_* 密钥
docker compose up -d postgres redis minio   # 数据层先行（init.sql 仅首启执行）
docker compose up -d                       # 应用层（无 GPU 环境）
docker compose --profile gpu up -d         # 含 vLLM（需 NVIDIA GPU + nvidia-container-toolkit）
```

### 6.2 扩展示例

```bash
# BFF 水平扩容（无状态，JWT 校验共享同一密钥）
docker compose up -d --scale bff=3          # 配合 nginx.conf 中 least_conn upstream 示例

# Agent Worker 扩容（吞吐受 LLM 生成速度约束，先扩 worker）
docker compose up -d --scale agent-worker=4

# 新增学科模型：复制 vllm-math 服务块改端口/模型名，
# 并在 .env 的 VLLM_ROUTES 中登记：{"edu-chem-7b": ["http://vllm-chem:8102"], ...}
# Gateway 按 subject 路由字段自动命中，无需改代码。

# 数据库读写分离（后续）：postgres 流复制只读副本，
# 应用层 DATABASE_URL 拆分读写两个 DSN；向量索引已建，召回差时按 init.sql
# 注释用 CONCURRENTLY 重建 ivfflat（lists 随数据量上调）。
```

### 6.3 健康检查约定

所有 FastAPI 服务必须暴露 `GET /health`（200 = 存活，compose healthcheck 依赖之）；vLLM 自带 `/health`；Nginx 提供 `/healthz`。`depends_on: service_healthy` 保证按依赖序启动。

---

## 7. 关键配置索引

| 配置 | 位置 |
|------|------|
| 数据库 DDL / 索引 / RLS | `infra/postgres/init.sql`（列定义以 `backend/shared/eduagent_shared/models.py` 为唯一事实源） |
| 共享 ORM / 设置 / 内部令牌校验 | `backend/shared/eduagent_shared/`（models / settings / security） |
| 共享存储（ApiKeyStore 等） | `backend/shared/eduagent_shared/stores/`（bff/proxy 的 `services/store.py` 为兼容再导出层） |
| 共享 DTO 契约（ChatMessage 等） | `backend/shared/eduagent_shared/contracts.py` |
| BFF→Agent 聊天翻译层 | `backend/bff/app/services/agent_chat.py` |
| 容器编排 / 网络隔离 | `docker-compose.yml` |
| 反向代理 / SSE / 限流 | `infra/nginx/nginx.conf` + `infra/nginx/eduagent_locations.conf` |
| 环境变量约定 | `.env.example` |
| 接口规范 | `docs/api/openapi.md` |
