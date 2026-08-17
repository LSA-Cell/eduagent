# EduAgent 接口文档

面向移动端（Flutter）的 API 规范。文档为 Markdown 描述（非完整 OpenAPI YAML），字段命名与数据库 DDL 一致。

## 0. 通用约定

- **Base URL**：`https://<host>/api/v1`（开发环境 `http://localhost/api/v1`，经 Nginx → BFF）
- **鉴权**：除注册/登录/刷新外，所有接口需 `Authorization: Bearer <access_token>`
- **编码**：`Content-Type: application/json; charset=utf-8`；SSE 接口为 `text/event-stream`
- **ID**：均为 UUID；时间均为 ISO 8601（UTC）
- **统一错误**：

```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "请求过于频繁，请稍后再试",
    "request_id": "req_01J8X3Z6K4M0T2N5P7Q9R1S3V5"
  }
}
```

常见错误码：`UNAUTHORIZED`(401) / `FORBIDDEN`(403) / `NOT_FOUND`(404) / `VALIDATION_ERROR`(422) / `RATE_LIMITED`(429) / `MODEL_UNAVAILABLE`(503) / `INTERNAL_ERROR`(500)

- **分页**：`?page=1&page_size=20`，响应统一：

```json
{ "items": [], "total": 137, "page": 1, "page_size": 20 }
```

---

## 1. 鉴权

### 1.1 注册 `POST /auth/register`

```json
// 请求
{
  "phone": "13800001111",
  "password": "Passw0rd!",
  "email": "alice@example.com",       // 可选
  "grade_level": "senior",            // primary/junior/senior/university
  "default_subject": "math"
}
// 响应 201
{
  "user": {
    "id": "3f6b9c2e-7a1d-4e5f-9c8b-0a1d2e3f4a5b",
    "phone": "13800001111",
    "grade_level": "senior",
    "is_teacher": false,
    "created_at": "2026-08-16T11:00:00Z"
  },
  "tokens": {
    "access_token": "eyJhbGciOi...",
    "refresh_token": "d2f9c1a4-...",
    "token_type": "Bearer",
    "expires_in": 900                  // access token 15 分钟
  }
}
```

### 1.2 登录 `POST /auth/login`

```json
// 请求（phone 或 email 二选一）
{ "phone": "13800001111", "password": "Passw0rd!" }
// 响应 200：同注册的 user + tokens 结构
```

### 1.3 刷新令牌 `POST /auth/refresh`

```json
// 请求
{ "refresh_token": "d2f9c1a4-..." }
// 响应 200（refresh token 旋转：旧 token 立即作废）
{
  "access_token": "eyJhbGciOi...(新)",
  "refresh_token": "9b7e2c10-...(新)",
  "token_type": "Bearer",
  "expires_in": 900
}
```

约束：Access Token 有效期 15 分钟；Refresh Token 7 天（`ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_DAYS`）。鉴权类端点在 Nginx 层有更严的按 IP 限流（5 r/s）。

---

## 2. 聊天与推理

### 2.1 流式聊天 `POST /chat/completions`（SSE，OpenAI 兼容扩展）

Nginx 对该路径关闭缓冲（`proxy_buffering off`，`read_timeout 600s`），逐 token 下发。

```json
// 请求
{
  "session_id": "a1b2c3d4-0000-4000-8000-000000000001",  // 可空；空则创建新会话
  "parent_id": null,                  // 可空；指定则在消息树上开分支（重新生成场景）
  "model": "auto",                    // auto=按学科路由；也可显式指定 edu-general-14b 等
  "subject": "math",                  // 学科路由字段：chinese/math/english/physics/...
  "inference_source": "internal",     // internal=自有 vLLM / external=用户自带 Key（经 Proxy）
  "stream": true,
  "messages": [
    { "role": "user", "content_type": "image", "content": "minio://eduagent-uploads/u/3f6b.../photo.jpg" }
  ],
  "tools": ["web_search", "mistake_extract"],   // 可空，声明本轮允许的工具
  "metadata": { "client": "flutter-ios/1.2.0" }
}
```

SSE 事件流（`data:` 逐行，OpenAI 兼容 chunk + EduAgent 扩展事件）：

```text
data: {"id":"chatcmpl-01","object":"chat.completion.chunk","choices":[{"delta":{"role":"assistant"},"index":0}]}

data: {"id":"chatcmpl-01","object":"chat.completion.chunk","choices":[{"delta":{"content":"这道"},"index":0}]}

data: {"id":"chatcmpl-01","object":"chat.completion.chunk","choices":[{"delta":{"content":"题考查"},"index":0}]}

data: {"event":"tool_card","card":{"type":"tool_result","tool":"web_search","title":"搜索结果","items":[{"title":"一元二次方程求根公式","url":"https://example.com"}]}}

data: {"id":"chatcmpl-01","object":"chat.completion.chunk","choices":[{"delta":{},"finish_reason":"stop","index":0}],"usage":{"prompt_tokens":512,"completion_tokens":186,"total_tokens":698},"x_message_id":"m_9c8b7a6f-...","x_inference_source":"internal","x_fallback":false}

data: [DONE]
```

说明：
- `subject` 决定 Gateway 学科路由（如 math → `edu-math-7b`）；`inference_source=external` 时经 Proxy 使用用户 Key。
- 发生模型降级时末帧 `x_fallback: true`，移动端应提示「已切换备用模型」。
- 完整 assistant 消息由 Agent 单侧落库（BFF 不写 messages），`x_message_id` 为消息 ID。
- 本端点为 BFF 翻译层：上游 Agent 9 事件协议被转译为 OpenAI 兼容 chunk；卡片/工具/计划等富事件不在本端点出现，需用 §2.1a。

### 2.1a 原生事件流 `POST /chat/stream`（SSE，Agent 9 事件协议）

请求体同 §2.1。BFF 原样透传 Agent `/v1/chat/stream` 事件流（不做 OpenAI 翻译），供已升级新协议的客户端消费工具调用、卡片、计划等富事件。

事件帧为三段式（`id:` / `event:` / `data:`），事件类型：`run.start`、`message.delta`、`tool.start`、`tool.result`、`card`、`plan.status`、`plan.outline`、`error`、`run.end`。

断线重放：携带 `Last-Event-ID: <event_id>` 请求头（或 `?run_id=<run_id>`）重连，服务端自该事件之后续传。Nginx 对本路径同样关闭缓冲（`proxy_buffering off`，`read_timeout 600s`）。

### 2.2 工具调用结果卡片结构

assistant 消息 `content_type` 可为 `tool_result / ppt_preview / lesson_plan_preview / exam_preview`，`content` 为 JSON 字符串，`metadata.card` 描述渲染卡片：

```json
// content_type = "ppt_preview" 示例（教案/试卷同构，字段见 §5 对应实体）
{
  "type": "ppt_preview",
  "title": "《二次函数》课堂导入",
  "task_id": "t_7d6c5b4a-...",
  "status": "generating",             // generating → completed/failed
  "slide_count": 12,
  "thumbnail_urls": ["minio://eduagent-generated/.../thumb_1.png"],
  "file_url": null,                   // 完成后填充（预签名 GET URL，≤1h）
  "actions": ["open", "regenerate", "export"]
}
```

```json
// content_type = "tool_result" 示例（拍照答疑的判定结果）
{
  "type": "tool_result",
  "tool": "mistake_extract",
  "title": "已收录到错题本",
  "mistake_id": "mk_2e3f4a5b-...",
  "knowledge_tags": ["一元二次方程", "判别式"],
  "next_review_at": "2026-08-17T11:00:00Z"
}
```

### 2.3 会话与消息管理

```http
GET    /chat/sessions?page=1&page_size=20        # 会话列表（按 updated_at 倒序）
POST   /chat/sessions                            # { "subject": "math" } → 201 创建
PATCH  /chat/sessions/{id}                       # 改标题 { "title": "函数专题" }
DELETE /chat/sessions/{id}                       # 204（级联删除消息）
GET    /chat/sessions/{id}/messages?limit=50&before=<message_id>   # 分页拉取（倒序）
```

---

## 3. 文件上传（预签名 URL）

文件一律**直传 MinIO**，不经过应用服务器；桶全部私有，URL 有效期 ≤ 1 小时。

### 3.1 申请上传 URL `POST /uploads/presign`

```json
// 请求
{
  "file_name": "homework.jpg",
  "content_type": "image/jpeg",
  "size_bytes": 1832451,
  "purpose": "chat_image"             // chat_image/knowledge_doc/avatar/mistake_image
}
// 响应 200
{
  "upload_url": "http://minio:9000/eduagent-uploads/u/3f6b.../a1b2.jpg?X-Amz-Signature=...",
  "object_key": "u/3f6b.../a1b2.jpg",
  "method": "PUT",
  "expires_in": 3600,                 // ≤ 1h
  "headers": { "Content-Type": "image/jpeg" }
}
```

移动端随后 `PUT upload_url` 直传二进制。超过大小/类型限制返回 `VALIDATION_ERROR`。

### 3.2 上传完成回调 `POST /uploads/confirm`

```json
// 请求
{ "object_key": "u/3f6b.../a1b2.jpg", "purpose": "knowledge_doc", "title": "必修一讲义.pdf" }
// 响应 200（purpose=knowledge_doc 时创建 knowledge_documents 并触发异步解析+embedding）
{
  "object_key": "u/3f6b.../a1b2.jpg",
  "verified": true,                   // 服务端已校验对象存在且属本人目录
  "document_id": "kd_8c7b6a5f-...",   // 非知识库用途时为 null
  "status": "processing"              // pending/processing/ready/failed
}
```

### 3.3 换取下载 URL `GET /files/{object_key}/url`

```json
// 响应 200（鉴权 + 归属校验，RLS 之外的二次防线）
{ "download_url": "http://minio:9000/eduagent-generated/...?X-Amz-Signature=...", "expires_in": 3600 }
```

---

## 4. 内容生成（PPT / 教案 / 试卷）

统一模式：**POST 创建 → 202 + task_id；GET 轮询 → 完成返回 file_url 与预览结构**。

### 4.1 创建生成任务

```http
POST /generate/presentations     # PPT
POST /generate/lesson-plans      # 教案
POST /generate/exam-papers       # 试卷
```

```json
// POST /generate/presentations 请求
{
  "topic": "光合作用的过程与意义",
  "title": "《光合作用》课件",        // 可空，自动生成
  "grade": "junior",
  "subject": "biology",
  "style": "fresh",                   // 模板风格（模板存 eduagent-templates 桶）
  "slide_count": 12
}
// 响应 202 Accepted（三类接口同构）
{
  "task_id": "t_7d6c5b4a-3b2a-4c1d-9e8f-0a1b2c3d4e5f",
  "resource_id": "pr_5e4d3c2b-...",   // presentations/lesson_plans/exam_papers 记录 ID
  "status": "pending",
  "poll_url": "/api/v1/generate/tasks/t_7d6c5b4a-3b2a-4c1d-9e8f-0a1b2c3d4e5f"
}
```

```json
// POST /generate/exam-papers 请求（额外字段示例）
{
  "title": "高一数学期中模拟卷",
  "grade": "senior", "subject": "math",
  "difficulty": "mixed",              // easy/medium/hard/mixed
  "question_spec": [ { "type": "choice", "count": 10 }, { "type": "solution", "count": 5 } ],
  "knowledge_scope": ["函数", "三角函数"]
}
```

### 4.2 轮询任务状态 `GET /generate/tasks/{task_id}`

```json
// 进行中 200
{ "task_id": "t_7d6c...", "status": "generating", "progress": 0.6, "stage": "排版生成中" }

// 完成 200（以试卷为例；PPT 为 slides+thumbnail_urls，教案为 content）
{
  "task_id": "t_7d6c...",
  "status": "completed",
  "resource_type": "exam_paper",
  "resource_id": "ep_1a2b3c4d-...",
  "file_url": "http://minio:9000/eduagent-generated/.../paper_student.pdf?X-Amz-Signature=...",
  "extra_file_urls": { "teacher": "http://minio:9000/.../paper_teacher.pdf?X-Amz-Signature=..." },
  "preview": {
    "questions": [
      { "no": 1, "type": "choice", "stem": "函数 f(x)=...", "options": ["A..","B.."], "answer": "C", "analysis": "…" }
    ]
  },
  "expires_in": 3600
}

// 失败 200
{ "task_id": "t_7d6c...", "status": "failed", "error": { "code": "GENERATION_FAILED", "message": "排版服务异常，可重试" }, "retryable": true }
```

记录查询（列表/详情）走对应 REST：`GET /generate/presentations`、`GET /generate/lesson-plans/{id}` 等，字段与 `presentations` / `lesson_plans` / `exam_papers` 表一致。

---

## 5. 导出接口 `POST /exports`

```json
// 请求：错题本导出为例（也支持 chat_session / study_plan / analysis_report）
{ "type": "mistake_book", "format": "pdf", "params": { "subject": "math", "range": "last_30d" } }
// 响应 202（导出为异步任务，轮询地址同 §4.2）
{ "task_id": "t_export_6f5e...", "status": "pending", "poll_url": "/api/v1/generate/tasks/t_export_6f5e..." }

// 轮询完成 200
{ "task_id": "t_export_6f5e...", "status": "completed", "file_url": "http://minio:9000/.../mistakes.pdf?X-Amz-Signature=...", "expires_in": 3600 }
```

---

## 6. 学情分析（教师端）

前置：调用者 `is_teacher=true` 且 `teacher_verified_at` 非空，否则 403。

### 6.1 创建分析 `POST /analyses`

```json
// 请求
{
  "class_name": "高一(3)班",
  "analysis_type": "error_trend",          // class_overview/student_detail/error_trend
  "target_student_id": null,               // student_detail 时必填
  "data_source": { "range": "last_30d", "subjects": ["math"], "include": ["mistakes", "chat_activity"] }
}
// 响应 202（异步生成）
{ "task_id": "t_ana_9d8c...", "analysis_id": "la_4b3a2c1d-...", "status": "pending" }
```

### 6.2 查询

```http
GET /analyses?page=1                       # 我发起的分析列表
GET /analyses/{id}                         # 详情
```

```json
// GET /analyses/{id} 完成态 200
{
  "id": "la_4b3a2c1d-...",
  "class_name": "高一(3)班",
  "analysis_type": "error_trend",
  "report_content": {
    "summary": "近 30 天班级错题集中于…",
    "top_knowledge_points": [ { "tag": "函数单调性", "mistake_count": 46 } ],
    "distribution": [ { "date": "2026-08-10", "count": 12 } ]
  },
  "file_url": "http://minio:9000/.../report.pdf?X-Amz-Signature=...",
  "created_at": "2026-08-16T11:00:00Z"
}
```

---

## 7. 错题本

```http
GET    /mistakes?subject=math&due=true&page=1   # due=true：只看 next_review_at<=now 的待复习
POST   /mistakes                                 # 手动收录
GET    /mistakes/{id}
PATCH  /mistakes/{id}
DELETE /mistakes/{id}                            # 204
POST   /mistakes/{id}/review                     # 复习打卡（艾宾浩斯推进）
```

```json
// POST /mistakes 请求 → 201
{
  "subject": "math",
  "question_text": "解方程 x²-5x+6=0",
  "question_image_url": null,                 // 或 minio:// 对象 key
  "user_answer": "x=2",
  "correct_answer": "x=2 或 x=3",
  "knowledge_tags": ["一元二次方程"],
  "source_message_id": "m_9c8b7a6f-..."       // 可空：来自聊天消息
}

// POST /mistakes/{id}/review 请求（quality: forgot/hard/good/easy 或布尔 remember）
{ "result": "good" }
// 响应 200：艾宾浩斯间隔序列 1d→2d→4d→7d→15d→30d
{ "id": "mk_2e3f...", "review_count": 3, "next_review_at": "2026-08-23T11:00:00Z" }
```

## 8. 记忆卡片（SM-2）

```http
GET    /flashcards?due=true&page=1              # due=true：due_date<=today
POST   /flashcards
PATCH  /flashcards/{id}
DELETE /flashcards/{id}
POST   /flashcards/{id}/review
```

```json
// POST /flashcards → 201
{ "subject": "english", "front_content": "abandon 的词义？", "back_content": "v. 放弃；抛弃", "knowledge_tags": ["必修一词汇"] }

// POST /flashcards/{id}/review（quality 0-5，SM-2 标准）
{ "quality": 4 }
// 响应 200
{ "id": "fc_7a6b...", "interval": 6, "ease_factor": 2.6, "due_date": "2026-08-22" }
```

## 9. 学习计划

```http
GET    /study-plans?status=active
POST   /study-plans                             # 可带 auto_generate=true 让 AI 排程
GET    /study-plans/{id}
PATCH  /study-plans/{id}                        # 含 status 流转 active/completed/archived
DELETE /study-plans/{id}
```

```json
// POST /study-plans → 201（auto_generate 时 generated_schedule 异步填充，轮询详情）
{
  "title": "高三数学一轮复习",
  "goal_description": "60 天过完函数与导数专题",
  "start_date": "2026-08-20",
  "end_date": "2026-10-18",
  "auto_generate": true
}
// 详情中的 generated_schedule 示例
{ "generated_schedule": { "weeks": [ { "week": 1, "tasks": [ { "day": "2026-08-20", "topic": "集合与常用逻辑", "minutes": 45 } ] } ] } }
```

---

## 10. 用户 API Key 管理

原则：**只写加密存储，永不回显**。服务端以 AES-256-GCM 加密（主密钥 `KEY_ENCRYPTION_MASTER_KEY`，90 天轮换）后存 `user_api_keys.encrypted_key`；任何响应只返回掩码。

```http
GET    /user/api-keys                           # 列表（掩码）
POST   /user/api-keys                           # 新增（只写）
PATCH  /user/api-keys/{id}                      # 调整预算 / 停用
DELETE /user/api-keys/{id}                      # 204
```

```json
// POST /user/api-keys 请求 → 201
{
  "provider": "openai",                         // openai/anthropic/qwen/...
  "api_key": "sk-xxxxxxxxxxxxxxxx",             // 仅本次请求传输，落库前加密
  "monthly_budget": 5000000                     // 单位：千 tokens/月，可空=不限
}
// 响应（掩码，永不回显完整 Key）
{
  "id": "k_5d4c3b2a-...",
  "provider": "openai",
  "key_hint": "sk-...xxxx",                     // 仅保留末 4 位
  "monthly_budget": 5000000,
  "used_budget": 0,
  "is_active": true,
  "created_at": "2026-08-16T11:00:00Z"
}

// PATCH /user/api-keys/{id}（不允许修改 api_key 本身；换 Key = 删除后重建）
{ "monthly_budget": 8000000, "is_active": false }
```

额度规则：请求 `inference_source=external` 时经 Proxy 转发并累计 `used_budget`；超预算或 `is_active=false` 时按降级策略回退 internal 轨（见架构文档 §4）。

---

## 11. 内部接口（服务间，不对移动端开放）

以下接口只在 `net_app` / `net_inference` 网络内可达，需携带 `X-Internal-Token: <INTERNAL_SERVICE_TOKEN>`：

| 接口 | 提供方 | 说明 |
|------|--------|------|
| `POST /v1/chat/completions` | Gateway :8001 | 与 §2.1 同构（OpenAI 兼容 + `subject` 路由字段），供 BFF/Agent 调用；移动端**禁止直连** |
| `GET /v1/models` / `GET /health` | Gateway / vLLM | 模型清单与活性探测 |
| `POST /proxy/v1/chat/completions` | Proxy :8004 | 携带 key_id，内存解密后转发第三方，响应含 `usage` 供计量 |
| `POST /render/pptx|docx|pdf` | Docgen :8005 | 入参为结构化内容 JSON，**只排版不生成内容**，产物写 MinIO |
| `POST /ocr` `/asr` `/tts` `/parse-document` | Multimodal :8003 | 纯结构化输入输出，无学科业务判断 |
