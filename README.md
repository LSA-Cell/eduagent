# EduAgent —— 教育领域移动端 AI Agent

面向 K12 至大学阶段的教育 AI Agent 移动应用（智能学习助手）。Mono-repo，包含 Flutter 移动端、FastAPI 微服务后端与完整基础设施配置。

## 技术栈

| 层级 | 技术 |
|------|------|
| 移动端 | Flutter 3.x + Riverpod + Hive/SQLite + Dio |
| BFF / 推理网关 / Agent 编排 / 多模态 / API 代理 / 文档生成 | FastAPI (Python 3.11+) + Celery |
| 自有模型推理 | vLLM (Docker, OpenAI 兼容接口) |
| 数据库 | PostgreSQL 15 + pgvector |
| 缓存/队列 | Redis 7 |
| 对象存储 | MinIO（预签名 URL） |
| 容器化 | Docker Compose（网络隔离） |
| 反向代理 | Nginx |

## 目录结构

```
├── mobile/                 # Flutter 移动端（iOS/Android 一套代码）
├── backend/
│   ├── shared/             # 公共库：配置/DB/Redis/加密/JWT/模型
│   ├── bff/                # BFF + API 网关：鉴权、校验、限流、路由转发
│   ├── gateway/            # 推理网关：双轨路由（自有 vLLM / 用户 API）、SSE、降级
│   ├── agent/              # Agent 编排：记忆、RAG、工具系统、内容生成引擎、Celery
│   ├── multimodal/         # ASR/OCR/TTS/文档解析/图片预处理
│   ├── proxy/              # 用户 API Key 代理（解密/转发/额度追踪）
│   └── docgen/             # 文档生成：python-pptx / python-docx / PDF
├── infra/
│   ├── postgres/init.sql   # 完整 DDL + pgvector + 索引 + RLS
│   └── nginx/nginx.conf    # 反向代理（SSL 终止/负载均衡）
├── docs/                   # 架构与 API 文档
│   ├── prompts/            # Master Prompt 设计文档
│   └── security/           # 安全审计报告（md 人读 + json 机读）
├── releases/               # 分发构建产物（APK/AAB，已 gitignore）
├── docker-compose.yml
└── .env.example
```

## 快速开始

```bash
cp .env.example .env   # 按需修改密钥与配置
docker compose up -d postgres redis minio
docker compose up -d   # 启动全部服务（vLLM 需要 GPU，可用 profile 控制）
```

移动端：

```bash
cd mobile
flutter pub get
flutter run
```

## 服务端口（默认）

| 服务 | 端口 |
|------|------|
| Nginx | 80/443 |
| BFF | 8000 |
| 推理网关 | 8001 |
| Agent 编排 | 8002 |
| 多模态 | 8003 |
| API 代理 | 8004 |
| 文档生成 | 8005 |
| vLLM | 8100+ |
| PostgreSQL | 5432 |
| Redis | 6379 |
| MinIO | 9000/9001 |

## 移动端视觉设计规范

本项目移动端遵循 Anthropic 官方 `frontend-design` Skill 的设计原则（辨识度优先、避免模板化"AI 审美"），要点如下：

### 角色种子色（`mobile/lib/app/theme.dart`）

| 角色 | 色值 | 取意 |
|------|------|------|
| 学生 | `#2B50AA` | 墨水蓝（钢笔水） |
| 家长 | `#C2703D` | 暖杏色（宣纸 / 木质课桌） |
| 教师 | `#1F6F5C` | 松绿（黑板） |

刻意避开 Material 官方色板色号，全 App 配色由种子色经 `ColorScheme.fromSeed` 派生，深浅色模式自适应。

### 字体

- 标题 / 展示字体：**HarmonyOS Sans SC**（华为开源，Medium / Bold 两字重，文件与授权见 `mobile/assets/fonts/`）
- 正文：系统默认字体，保证阅读性与包体积

### 签名元素纪律

全 App 唯一的"渐变 + 光晕"元素是**语音录制按钮**（`voice_page.dart`）。其余品牌元素
（登录 Logo、聊天气泡、发送键、头像、步骤序号、AppBar 图标）统一为**纯描边、无色彩填充**，
让视觉重心聚焦于签名时刻。

## 测试

- 后端：各服务目录下 `pytest`
- 移动端：`cd mobile && flutter test`
- 集成测试：`backend/gateway/tests/test_integration.py`（推理网关降级）

## 文档

- 架构说明：`docs/architecture.md`
- 接口规范：`docs/api/openapi.md`
