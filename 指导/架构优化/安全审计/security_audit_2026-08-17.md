# EduAgent 全仓库安全审计报告（P0 级排查）

审计时间：2026-08-17
审计范围：backend（bff / gateway / agent / multimodal / proxy / docgen / shared）、mobile、infra、docker-compose.yml
方法：自动化扫描（secrets / OWASP / deps）+ 人工审查认证、密钥、转发、存储、部署面

## 结论速览

| 级别 | 数量 | 说明 |
|---|---|---|
| P0 | 2 | 默认密钥无生产校验 + 内部端口全暴露（组合可致全系统失陷） |
| P1 | 5 | SSRF、Refresh Token 无撤销、上传无大小限制、注册无短信验证、CORS 全开 |
| 误报 | 7 | 自动扫描 high 项均为测试常量/Dart 键名/中文文本熵值误报 |

---

## P0-1 默认密钥随代码分发，且无生产环境启动校验（fail-open）

**位置**：`backend/shared/eduagent_shared/config.py:36,47,54,59,70`

```
postgres_password: str = "change_me_postgres"
minio_secret_key: str = "change_me_minio"
jwt_secret: str = "change_me_jwt_secret"
key_encryption_master_key: str = "change_me_base64_32bytes_master_key"
internal_service_token: str = "change_me_internal_service_token"
```

**问题**：所有核心密钥的默认值是写死在代码库里的公开占位串。全仓库搜索确认**没有任何** "APP_ENV=prod 时禁止使用默认值" 的启动检查。一旦运维漏改 .env 即裸奔上线：

- `jwt_secret` 已知 → 任何人可伪造任意用户的 access/refresh token，全平台账户沦陷；
- `internal_service_token` 已知 → 可直连任意内部服务（agent / proxy / docgen），proxy 会用平台用户绑定的真实厂商 Key 替你调模型（直接烧钱）；
- `key_encryption_master_key` 已知且占位串走 SHA-256 派生（crypto.py:42）→ 数据库里所有用户 API Key 的 AES-GCM 加密形同虚设。

**修复**：
1. `Settings` 增加启动校验：`app_env != "dev"`（或显式 `prod`）时，上述任一字段为默认值/空值 → 直接拒绝启动（`model_validator` 抛错）。
2. `crypto.py:_load_key` 在非 dev 环境遇到非 32 字节 base64 密钥时拒绝派生、直接报错。

## P0-2 docker-compose 把全部内部服务与数据层端口绑定到宿主机 0.0.0.0

**位置**：`docker-compose.yml:28-29, 50-51, 73-75, 127-128, 169-170, 200-201, 284-285, 305-306, 327-328`

**问题**：nginx 的架构注释明确写着"仅接入 net_edge，只可达 bff，永远接触不到数据层与推理层"，但 compose 同时把 Postgres(5432)、Redis(6379)、MinIO(9000/9001)、gateway(8001)、agent(8002)、multimodal(8003)、proxy(8004)、docgen(8005) 全部 publish 到宿主机所有网卡。公网部署时攻击者完全绕过 nginx 边界，直连内部服务（配合 P0-1 的默认内部令牌 = 无认证）。Redis 本身无密码，5432 用默认口令。

**修复**：
1. 除 nginx(80/443) 与 bff(如必须) 外，所有端口映射改为 `127.0.0.1:xxxx:xxxx` 或直接删除（compose 内部网络本就可通）。
2. 或拆分 `docker-compose.dev.yml`（暴露端口）与 `docker-compose.prod.yml`（仅 80/443）。
3. Redis 增加 `requirepass`，Postgres 禁止容器外 trust。

---

## P1-1 SSRF：multimodal 任意 URL 抓取无过滤

**位置**：`backend/multimodal/app/routers/common.py:18-27`（`resolve_bytes`，被 parse/ocr/asr/grade 共用）

`file_url` 直接 `client.get(url)`，无 scheme 白名单（允许 file:// 以外的 ftp/gopher 等由 httpx 兜底但 http(s) 内网全通）、无私网 IP 阻断。可打 `http://169.254.169.254/`（云元数据）、`http://localhost:8004/proxy/...`（内网服务）。当前该接口需内部令牌，缓解因素是 BFF 暂未把用户 URL 透传过来（users.py 仅登记 file_url 不抓取）；但一旦任何调用方传入用户可控 URL 即被打穿。

**修复**：解析 URL → 仅允许 http/https → DNS 解析后校验目标 IP 不在 RFC1918/169.254/127.0.0.0/8；或对 MinIO 内网地址走专用客户端而非公网 GET。

## P1-2 Refresh Token 无旋转撤销，无法登出

**位置**：`backend/bff/app/routers/auth.py:72-86`

注释声称"refresh token 旋转"，实际只校验签名后签发新对，**旧 refresh token 在 7 天有效期内持续可用**，无 jti 黑名单、无服务端存储、无撤销接口。Token 泄露后改密码/登出均无法阻断。

**修复**：refresh token jti 落 Redis（7 天 TTL），刷新时校验+作废旧 jti；提供 logout 撤销。

## P1-3 multimodal 上传/下载无大小限制（内存耗尽 DoS）

**位置**：`backend/multimodal/app/routers/common.py:14, 23`

`upload.read()` 与 URL 下载均全量读入内存，无上限（对照：`agent/app/routers/kb.py:34` 有 50MB 限制）。大文件或恶意 URL 响应可打爆 worker 内存。

**修复**：检查 `Content-Length` + 流式读至上限（如 50MB），超限 413。

## P1-4 手机号注册无短信验证

**位置**：`backend/bff/app/routers/auth.py:42-57`、`schemas.py:34-39`

手机号+密码即可注册任意手机号（占号、冒名），无验证码校验。nginx auth 限流（burst=10）只能缓解不能根治。

**修复**：接入短信验证码（或至少图形/行为验证码 + 同号频控）。

## P1-5 BFF CORS 全放开

**位置**：`backend/bff/app/main.py:58` `allow_origins=["*"]`

移动端确实无浏览器跨域问题，但生产若未来有 Web 端，`*` + Authorization 头会让任意站点代发请求。注释已自知"生产收敛"，需落成配置项而非注释。

---

## 确认无问题项（已人工复核）

- 内部令牌校验全部 `secrets.compare_digest` 常量时间比对，且 fail-closed（空期望值拒绝）；
- JWT：PyJWT 指定算法白名单（HS256），无 alg=none 风险；access 15min 合理；
- 密码 bcrypt 哈希；72 字节截断有显式处理；
- 用户 API Key：AES-256-GCM、nonce 随密文、支持双钥轮换、日志只记掩码后 4 位、上游错误消息透传前对 Key 脱敏（proxy/chat.py:48）——设计良好；
- SQL 全走 SQLAlchemy ORM/参数化 `text()`，无拼接注入面；RLS 上下文 `app.current_user_id` 参数化注入；
- proxy 上游 URL 由 provider adapter 固定，不受用户输入控制；
- docgen 对象键为服务端生成（prefix+task_id），无路径遍历；MinIO bucket 私有，预签名 1h；
- WS 鉴权 fail-closed（无效 token 4401 关闭）；
- `.env` 未入 git，`.gitignore` 已排除 `.env`/`*.pem`/`*.key`；依赖扫描（npm/pip）无已知漏洞；
- nginx 已有 auth 端点专项限流、SSE 禁缓冲、最小暴露面设计（被 P0-2 的 compose 端口映射架空）。

## 自动扫描原始结果

- `security_report.json`（secrets+owasp，high 及以上 7 项，均误报）
- `security_owasp.json`（owasp 全级别，0 发现）
