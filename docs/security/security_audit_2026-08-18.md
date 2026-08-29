# EduAgent 安全审计报告（Wave C/D 落地后复扫）

- **扫描时间**：2026-08-18（本地）/ 2026-08-17T19:48Z（UTC）
- **扫描范围**：全项目（`backend/`、`mobile/lib/`、`infra/`、`docs/`），排除 `.venv`、`build`、`.git`
- **扫描器**：code-safety-audit（secrets 正则+熵值 / OWASP 模式 / deps 依赖漏洞）+ pip-audit 2.10.0 对 `backend/.venv` 实装环境复核
- **上一版报告**：`security_audit_2026-08-17.md`（Wave C/D 落地前）

## 总结论

| 模块 | 结果 |
|------|------|
| 依赖漏洞（pip-audit 实装环境） | ✅ **零已知漏洞**（仅本地包 `eduagent-shared` 因不在 PyPI 跳过，属预期） |
| OWASP 模式（SQL 注入/XSS/命令注入/反序列化/路径遍历/配置错误/SSRF/弱加密） | ✅ **零发现**（覆盖 Wave C/D 全部新增模块） |
| 密钥泄露 | 9 高 + 1 中，**逐条人工复核后均为误报或测试夹具**，无真实密钥泄露 |

**编排层红线复核**：编排服务内无模型 API 直连代码、无 API Key 明文（密钥加解密在 `backend/proxy` 内闭环）、`.env` 未被 git 追踪且已在 `.gitignore` 第 21 行排除。

## 发现明细（10 条，全部复核）

### 误报（5 条，无需处理）

| # | 位置 | 说明 |
|---|------|------|
| 1 | `mobile/lib/core/storage/secure_storage.dart:15-16` | `_kAccessToken = 'access_token'` 是 Hive **存储键名**，不是密钥值 |
| 2 | `security_report.json:61,70` | 扫描器匹配到了**上一版报告自身**引用的上述代码片段（自引用噪声） |
| 3 | `backend/agent/tests/test_tool_summary.py:88` | 中文作文测试文本，用于验证"摘要不泄露用户隐私原文"（熵值误报，恰好证明该测试在工作） |

### 测试夹具（3 条，低风险，可保留）

| # | 位置 | 说明 |
|---|------|------|
| 4 | `backend/agent/tests/conftest.py:19` | `INTERNAL_TOKEN = "test-internal-token"` 测试桩 |
| 5 | `backend/agent/tests/test_voice.py:73` | `token="wrong-token"` 负数测试用例 |
| 6 | `backend/scripts/smoke_integration.py:113` | `password = "smoke-test-123"` 本地冒烟脚本 |

### 建议改进（1 条，低优先级）

| # | 位置 | 说明 |
|---|------|------|
| 7 | `backend/scripts/smoke_integration.py:31` | `INTERNAL_TOKEN = "change_me_internal_service_token"  # 与 .env 一致`：当前是占位符，无泄露。但若有人填入真实 token 会进入代码库。**建议改为 `os.environ["INTERNAL_TOKEN"]` 从环境读取**，从机制上杜绝回填风险。 |

## 与上一版（08-17）对比

- 上一版审计时 Wave C/D（plan/ask/queue/todo/stop_hooks/dream/hooks/gates/styles/observe 等 19 项机制）尚未落地；本次复扫**新增代码零 OWASP 发现、零真实密钥泄露**。
- `confirm_token` 处理符合安全设计：只存 SHA-256 哈希（`plan/manager.py`）、一次性消费、30 分钟过期、跨用户拒绝。
- 内容审核 fail-closed、护栏链强制执行、SSE 事件不携带堆栈/内部路径等红线在代码层均有落实（见同日机制核查结论）。

## 附件

- 机器可读报告：`security_scan_full.json`
- 扫描退出码：1（有发现）；人工复核后实际风险：**低**
