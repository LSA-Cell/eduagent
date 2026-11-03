# EduAgent 移动端（mobile/）

教育领域 AI Agent 的 Flutter 客户端，iOS / Android 一套代码。

## 技术栈

- Flutter 3.x + Dart 3，Material 3
- 状态管理：flutter_riverpod（StateNotifier / StateProvider / FutureProvider）
- 路由：go_router（StatefulShellRoute 底部导航）
- 网络：dio。API client 为**手写**（`lib/core/network/api/`），不依赖 build_runner /
  retrofit 代码生成，降低构建复杂度。SSE 流式由 `lib/core/network/sse_client.dart`
  基于 `ResponseType.stream` 逐行解析 `data:` / `event:`。
- 本地存储：Hive（设置 / 草稿 / 离线队列）、sqflite（会话与消息结构化缓存）、
  flutter_secure_storage（Access/Refresh Token 与用户 API Key，**不明文落盘**）。
- 渲染：flutter_markdown + flutter_math_fork（`$...$` LaTeX）、photo_view、fl_chart。

## 字体授权

本应用标题/展示字体使用 **HarmonyOS Sans SC**（Copyright 2021 Huawei Device Co., Ltd.），
依据 HarmonyOS Sans Fonts License Agreement 以免版税方式嵌入，未做修改。
完整授权文本见 `assets/fonts/LICENSE.txt`。

## 运行

```bash
cd mobile
flutter pub get
# 指定后端 BFF 地址（默认 http://localhost:8000）
flutter run --dart-define=API_BASE_URL=http://<host>:8000
```

## 测试

```bash
flutter test
```

包含：教育工具栏折叠/展开与身份重排 widget 测试、`<step>` 解析单测、
SSE 解析器单测、消息模型 JSON 序列化单测。

## 目录结构

```
lib/
├── main.dart                  # ProviderScope、路由、主题入口
├── app/                       # router.dart / theme.dart（学生/教师主题色）
├── core/
│   ├── config/app_config.dart # baseUrl 环境配置（--dart-define）
│   ├── network/               # dio_client / sse_client / api/*（手写 API client）
│   └── storage/               # secure_storage / local_db（Hive + sqflite）
├── features/                  # auth / chat / voice / mistakes / flashcards /
│                              # plans / teacher / profile
└── models/                    # 手写 fromJson/toJson 数据模型（纯 Dart，便于单测）
```

## 约定

- 工具栏（`edu_toolbar.dart`）由 `userModeProvider` 驱动重排：教师模式优先展示
  制作 PPT / 教案 / 试卷 / 学情分析；学生模式展示学习工具在前。
- 直接触发型工具（公式助手 / 化学方程式 / 作文辅导 / 知识点检测 / 学习计划 /
  PPT / 教案 / 试卷 / 学情分析）点击后弹出参数收集 Sheet
  （`features/chat/widgets/tool_sheets.dart`、`generation_sheets.dart`），提交后由
  `chatProvider` 在聊天区插入"正在使用…"系统消息并调用后端工具；PPT / 教案 / 试卷
  为异步生成任务（`POST /teacher/{presentations|lesson-plans|exam-papers}` 创建 +
  2s 轮询 `poll_url`，最长 60s），完成后追加对应预览卡片消息；公式/化学/作文等
  直接触发型工具转为一条用户消息走 `POST /chat/completions` 流式触发（无独立
  /tools/{id} 接口，路径契约见 `指导/后端设计/API协议/contract.md`）。公式助手结果写入
  `chatInputInsertProvider`，由输入框插入光标处。
- Token 刷新与 401 重试统一在 `dio_client.dart` 的 `QueuedInterceptor` 中完成。
