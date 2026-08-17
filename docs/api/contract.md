# API 契约对照表（移动端 ↔ BFF ↔ 下游）

移动端 base：`{API_BASE_URL}/api/v1`（见 `mobile/lib/core/config/app_config.dart`）。
BFF 只做鉴权 + 转发；业务逻辑在 Agent 编排服务。下游路径与 BFF 路径一致（`forward_to_agent` 透传，携带 `X-Internal-Token` + `X-User-Id`）。

## 鉴权 / 用户（BFF 本地，无下游）

| 移动端方法 | 移动端调用 | BFF 路由 | 说明 |
|---|---|---|---|
| AuthApi.login | POST /auth/login | 同左（BFF 本地） | 入参 `{phone, password}`；响应 `{access_token, refresh_token, ...}`（不含 user，客户端再取 /users/me） |
| AuthApi.register | POST /auth/register | 同左（BFF 本地） | 入参 `{phone, password}`；响应 `{user, tokens}`；非学生身份注册后客户端补 PUT /users/me/mode；nickname 仅本地保留（users 表无该列） |
| AuthApi.me / UserApi.getProfile | GET /users/me | 同左 | 响应 UserOut：`{id, phone, email, grade_level, default_subject, is_teacher, is_paid, user_mode, created_at}` |
| AuthApi.logout | —（不发请求） | — | JWT 无状态，仅本地清 token |
| AuthInterceptor（dio_client 自动刷新） | POST /auth/refresh | 同左（BFF 本地） | 入参 `{refresh_token}`；响应 `{access_token, refresh_token, ...}`（snake_case，勿用 camelCase） |
| UserApi.updateProfile | PUT /users/me/mode；PUT /users/me | 同左 | `mode` → /me/mode；`grade`→grade_level、`subjects`→default_subject（首项）→ PUT /me；`selectedModel`/`nickname` 不下发 |
| UserApi.listModels | GET /models | 转发 → 推理网关 GET /v1/models | 响应 `{object: list, data: [{id, ...}]}`；客户端前置 `auto` |
| UserApi.listApiKeys / addApiKey | GET/POST /users/me/api-keys | 同左（BFF 本地） | POST 入参 `{provider, api_key}`；provider ∈ openai/deepseek/claude/gemini；响应 ApiKeyOut（仅掩码） |
| UserApi.deleteApiKey | DELETE /users/me/api-keys/{key_id} | 同左 | 按记录 id 删除（ApiKeyEntry.id） |
| TeacherApi.uploadCertification | POST /users/me/teacher-certification | 同左 | 入参 `{file_url, note?}`；202 占位受理（审核流待迭代，暂不变更 is_teacher） |

## 聊天（BFF 本地 + 推理网关）

| 移动端方法 | 移动端调用 | BFF 路由 | 说明 |
|---|---|---|---|
| ChatApi.listConversations | GET /chat/sessions | 同左 | 响应 `Page<SessionOut>`：`{items, total, page, page_size}` |
| ChatApi.createConversation | POST /chat/sessions | 同左 | 入参 `{title?}`，201 |
| ChatApi.listMessages | GET /chat/sessions/{id}/messages | 同左 | 响应 `Page<MessageOut>`：`session_id/content_type/content/metadata/created_at` |
| ChatApi.streamChat | POST /chat/completions | 转发 → 网关 POST /v1/chat/completions（SSE 透传） | 入参 `{session_id?, messages: [{role, content}], stream: true}`；附件以 text 分段携带 URL；`local_` 前缀会话 id 不下发；SSE 为 OpenAI 格式，客户端解 `choices[0].delta.content` |

## 业务资源（BFF 转发 → Agent 编排）

| 移动端方法 | 移动端调用 | Agent 下游 | 说明 |
|---|---|---|---|
| UserApi.listMistakes | GET /mistakes | 同左 | 响应 `{items: [...]}`，字段 wrong_answer/correct_answer/created_at |
| UserApi.reviewMistake | POST /mistakes/{id}/review | 同左 | 入参 `{correct}`，艾宾浩斯推进 |
| UserApi.mistakeVariations | POST /mistakes/{id}/variations | 同左（SSE） | 自定义事件 `{type: delta/done/error}`；BFF 目前整段缓冲返回（非逐 chunk） |
| UserApi.listFlashcards | GET /flashcards | 同左 | `{items}`，到期时间字段为 `due_date` |
| UserApi.reviewFlashcard | POST /flashcards/{id}/review | 同左 | rating 映射：forgotten→again / fuzzy→fuzzy / remembered→good |
| —（移动端暂未用） | POST /flashcards、POST /flashcards/generate、PATCH/DELETE /flashcards/{id} | 同左 | BFF 已转发，供后续卡片管理 UI 使用 |
| UserApi.createPlan | POST /study-plans | 同左 | 入参 `{goal_description, current_level, days}`（title/subject/dailyMinutes/deadline 由客户端换算）；202 `{id, task_id, poll_url}`，排程异步生成 |
| UserApi.listPlans | GET /study-plans | 同左 | 详情含 `generated_schedule [{day, task, duration}]` + `completed_days`；StudyPlan.tasks 由其映射（task.id = day） |
| UserApi.updatePlanTask | POST /study-plans/{id}/checkin | 同左 | 入参 `{day, note?}`，幂等只增；取消勾选（done=false）不下发 |
| ToolApi.uploadFile | POST /kb/documents/upload-url → PUT 预签名地址 | Agent（封装 MinIO 预签名） | 二阶段上传；⚠️ Agent 下游该路由尚未实现（见"遗留项"） |
| UserApi.evaluateVoice | POST /voice/evaluate | 同左 | 入参 `{scenario, transcript}`；LLM 输出四维评分+总评，解析失败兜底 0 分；ASR 未接入前 transcript 传空串 |
| TeacherApi.analytics | GET /analytics/knowledge-mastery | 同左 | 响应 `{user_id, matrix: [{knowledge_tag, mastery(0~1), ...}]}`；客户端 mastery×100 |
| TeacherApi.generateReport | POST /teacher/learning-analyses → 轮询 GET /teacher/learning-analyses/{id} | 同左（教师限制） | 入参 `{class_name, analysis_type, data_source}`；202 异步；终态 status=completed |
| TeacherApi.listLibrary | GET /teacher/lesson-plans、/teacher/presentations、/teacher/exam-papers | 同左（教师限制） | kind 映射：lesson_plan/courseware/exam；客户端统一补 title/updatedAt |
| ToolApi.createGenerationTask | POST /teacher/presentations、/teacher/lesson-plans、/teacher/exam-papers | 同左（教师限制） | toolId 映射：make_ppt/make_lesson_plan/make_exam；返回 poll_url |
| ToolApi.getGenerationTask | GET {poll_url} | 同左 | status：generating → ready（PPT）/ completed（教案/试卷/分析）/ failed |
| chatProvider.invokeTool / invokeToolWithFile | POST /chat/completions | → 网关 | 无独立 /tools/{id} 接口；工具需求转为一条用户消息流式触发 |

## 约定

- **分页**：BFF 自有分页为 `Page{items, total, page, page_size}`；Agent 列表为 `{items: [...]}`。客户端统一取 `items`。
- **时间戳**：后端一律 ISO8601 snake_case 键（created_at 等）；模型 fromJson 保留 camelCase 回退以兼容本地缓存（LocalDb）。
- **教师资源**：`/teacher/*` 由 BFF `require_teacher` 拦截（users.is_teacher）。
- **Agent 别名**：学情分析在 Agent 同时挂在 `/teacher/analyses` 与 `/teacher/learning-analyses`；移动端与 BFF 统一只用 `/teacher/learning-analyses`。

## 遗留项

1. `POST /kb/documents/upload-url`：BFF 转发已就位，Agent 下游路由尚未实现（需在 Agent 加 MinIO 预签名）。移动端 uploadFile 已实现完整二阶段逻辑，下游就位即可用。
2. `/mistakes/{id}/variations` 经 BFF 为整段缓冲返回（SSE 内容一次性下发），如需打字机效果需在 BFF 引入流式转发。
3. `nickname` / `subjects`（多选）/ `selectedModel` 服务端无字段，仅本地保留；如需云端同步要给 users 表加列。
4. 学情看板的成绩趋势（scoreTrend）与题型分布（typeDist）暂无数据源，看板只展示知识点掌握度矩阵。
