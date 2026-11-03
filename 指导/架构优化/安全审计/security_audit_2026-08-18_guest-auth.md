# EduAgent 安全审计报告（游客登录 + 鉴权增强落地后复扫）

- **扫描时间**：2026-08-18（本地）
- **扫描范围**：全项目（`backend/`、`mobile/lib/`、`infra/`、`docs/`），排除 `.venv`、`build`、`.git`、`node_modules`、`.dart_tool`、`__pycache__`
- **扫描器**：code-safety-audit（secrets 正则+熵值 / OWASP 模式 / deps 依赖漏洞）
- **上一版报告**：`security_audit_2026-08-18.md`（Wave C/D 复扫）
- **本次变更**：游客登录、登出/吊销、设备管理、短信验证码、邮箱/第三方登录、密码维护、登录锁定与图形验证码（BFF 鉴权域内，未触碰服务边界）

## 总结论

| 模块 | 结果 |
|------|------|
| OWASP 模式（含全部新增 auth 代码） | ✅ **零发现** |
| 密钥泄露 | 19 高 + 1 中，逐条复核**与上一版完全同源**（自引用报告/测试夹具/存储键名），新增代码零发现 |
| 依赖漏洞 | 未新增第三方 Python 依赖（验证码 SVG 纯标准库；Apple JWKS 复用 PyJWT）；移动端新增 `flutter_svg`（纯渲染库） |

## 本次新增代码的安全设计核对

| 项 | 落实情况 |
|---|---|
| 短信验证码 | Redis 只存 SHA-256 哈希；60s 重发间隔 + 日上限 10 条 + 试错 5 次；一次性消费；dev_code 仅 dev/test 返回 |
| 短信 provider | `log` 仅限 dev/test，生产启动 fail-closed 拒绝（Settings 校验器）；webhook 携带内部服务令牌 |
| 图形验证码 | 答案哈希存 Redis 5 分钟、一次性消费；SVG 无外部资源引用 |
| 登录锁定/验证码闸口 | Redis 计数；验证码错误同样计入失败（否则锁定永不生效）；Redis 故障 fail-open 并告警（与限流器同策略，Nginx 按 IP 5 r/s 兜底） |
| 登出/踢下线 | access jti 黑名单（TTL 至自然过期，登出即刻生效）+ 设备会话吊销（refresh 真失效） |
| 游客账号 | 真实 user 行（is_guest），phone/password 可空；同设备 ID 找回；转正需短信验证码且校验手机号唯一 |
| 第三方登录 | 未配置凭据 503 fail-closed；微信 code 服务端换 openid；Apple identity_token 走 JWKS 验签 + aud 校验；dev_openid 直通仅 dev/test |
| 密码规则 | 前后端统一 8-128 位（登录入参不限长度只做非空校验）；bcrypt 72 字节截断逻辑不变 |
| 红线复核 | 全部逻辑在 BFF 鉴权职责内；Gateway/Agent/Proxy/Docgen 未动；无密钥明文、无新增外联（除配置的短信 webhook / 微信 / Apple 官方端点） |

## 发现明细（19 条，全部复核为误报/测试夹具/自引用）

与 `security_audit_2026-08-18.md` 同源：扫描器命中了**前两版报告自身**（security_audit_*.md、security_report.json、security_scan_full.json 自引用）、测试夹具（`test_voice.py` / `conftest.py` / `smoke_integration.py`）、`secure_storage.dart` 的 Hive 键名常量、`test_tool_summary.py` 的中文作文熵值误报。**新增代码零命中。**

## 附件

- 机器可读报告：`security_scan_full.json`
- 扫描退出码：1（有发现）；人工复核后实际风险：**低**
