# Billing v1 Spec —— 会员订阅与支付

> 版本：v1.0 ・ 2026-09-10
> 目标：把「平台统一推理」（platform fallback）从免费兜底变成可售卖的订阅商品，
> 形成 Cursor 式会员制：免费档小额体验额度，付费档大额度 + 高级模型准入。

---

## 1. 范围边界（严格执行，逐条核对）

### 1.1 本期实现（In Scope）

| # | 模块 | 内容 |
|---|------|------|
| S1 | 数据层 | `plans` / `subscriptions` / `orders` / `quota_periods` 四表 + 索引 + RLS（沿用 `app.current_user_id` GUC 策略写法）+ plans 种子数据 |
| S2 | shared 层 | 4 个 ORM 模型（`eduagent_shared/models.py`）+ billing 配置项（`config.py`） |
| S3 | billing 服务 | 新微服务 `backend/billing/`（端口 8006，仅 `net_app`）：套餐查询、订阅查询、下单、webhook、手工开通（admin grant）、惰性过期、`users.is_paid` 同步 |
| S4 | 支付适配层 | `PaymentProvider` 协议 + `MockPaymentProvider`（dev/test 用）；真实渠道仅留接口 |
| S5 | proxy 额度闸门 | 平台兜底调用前查额度、超限 402 带升级引导；调用后按实际成本原子记账 |
| S6 | BFF 代理 | `routers/billing.py`：JWT 用户 → `X-User-Id` + 内部令牌转发 billing 服务 |
| S7 | 基础设施 | docker-compose 注册 billing（8006 绑 127.0.0.1）；nginx 新增 `/webhooks/billing/` 公网入口；`.env.example` 新配置 |
| S8 | 移动端 | `features/billing/`：套餐页、订阅状态卡片、额度进度条（走 BFF；支付按钮为 mock 确认） |
| S9 | 测试 | billing 服务测试 + proxy 闸门测试（InMemory 仓储 + httpx MockTransport，全程离线） |

### 1.2 明确不做（Out of Scope）

- **真实支付渠道**：Apple IAP 收据校验、微信/支付宝/Stripe SDK、RevenueCat —— 只定义适配器接口与 mock 实现，接真实渠道为 v1.1 任务
- 退款、发票、优惠码/折扣、按比例折算（proration）、套餐升降级
- 订阅过期定时扫描任务（以**惰性过期**代替：读取时 `period_end < now` 即视为 expired）
- plans 的运营后台管理 API（种子数据由 SQL 维护，改套餐 = 改 SQL 重部署）
- gateway / agent / multimodal / docgen 服务零改动
- 移动端真实 IAP 集成（商店 SDK 接入为 v1.1）
- iOS/Android 商店上架合规材料（隐私政策、退款条款文案）

---

## 2. 关键设计决策（不可偏离）

1. **额度单位 = USD 成本美分**，与 `cost_ledger` / `pricing.py` 完全一致；
   套餐售价以 CNY 分展示，两者解耦（定价是运营决策，成本是技术度量）。
2. **软闸门**：proxy 平台兜底先查 `used_cents < quota_cents` 放行，调用后按实际成本
   原子累加；允许单次轻微超支，换取无预估复杂度的实现。
3. **免费档默认覆盖所有用户**：`quota_periods` 当月行不存在时按 free plan 惰性创建，
   无需注册时建行。
4. **模型准入**：`plan.allowed_models`（JSONB 数组）；free 档仅允许低成本模型；
   空数组 = 全开放。平台兜底型号必须 ∈ allowed_models。
5. **`is_paid` 同步**：订阅激活 → `users.is_paid = true`，过期/取消 → false。
   `RateTierResolver` 限流逻辑零改动，天然生效。
6. **webhook 安全**：HMAC-SHA256 签名头 `X-Billing-Signature = hex(hmac(secret, raw_body))`；
   验签失败 401。幂等靠 `orders.channel_txn_id` 唯一约束 + 状态机守卫。
7. **错误语义**：额度耗尽 = HTTP 402 `quota_exceeded`，payload 带 `upgrade_hint: true`
   与当前套餐信息，移动端据此弹升级引导。
8. **内部服务边界**：billing 仅接受 `X-Internal-Token` 调用（同 proxy/gateway）；
   唯一公网入口是 nginx 的 `/webhooks/billing/{channel}`（无内部令牌，靠渠道签名验真）。
9. **RLS**：`subscriptions` / `orders` / `quota_periods` 含 `user_id`，策略与其他用户表一致；
   `plans` 为全局只读表，不加 RLS。

---

## 3. 数据模型

```sql
plans            id PK(VARCHAR) | name | price_cents | currency=CNY | monthly_quota_cents(BIGINT, USD成本美分)
                 | allowed_models(JSONB) | features(JSONB) | is_active
subscriptions    id UUID PK | user_id FK | plan_id FK | status(active/canceled/expired)
                 | channel | channel_sub_id | current_period_start/end | auto_renew
orders           id UUID PK | user_id FK | plan_id FK | amount_cents | currency
                 | channel | channel_txn_id UNIQUE | status(pending/paid/refunded/failed) | paid_at
quota_periods    (user_id, period_start) 复合PK | quota_cents | used_cents
```

种子数据（初始三档，可随时 SQL 调整）：

| plan | 月价 | 月额度（USD 成本） | 模型准入 |
|------|------|------|------|
| free | ¥0 | $3（300¢） | 仅低成本模型（deepseek-chat 等） |
| pro | ¥68 | $30（3000¢） | 全平台模型 |
| teacher_pro | ¥98 | $50（5000¢） | 全平台模型 + 教师功能 |

## 4. 接口契约

### billing 服务（内部，X-Internal-Token；用户身份经 X-User-Id 头注入）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/billing/plans` | 在售套餐列表（公开展示数据，无需 X-User-Id） |
| GET | `/billing/subscription` | 当前订阅 + 本期额度用量（惰性过期在此触发） |
| POST | `/billing/orders` | 下单 `{plan_id, channel}` → 返回订单 + 支付参数（mock 直接可 confirm） |
| POST | `/billing/webhooks/{channel}` | 渠道回调（无内部令牌，HMAC 验签；mock 渠道事件：payment_succeeded / subscription_canceled） |
| POST | `/billing/admin/grant` | 手工开通/续期 `{user_id, plan_id, days}`（仅 internal token，测试与客服用） |

### BFF 代理（`/api/v1/billing/*`，用户 JWT）

| 方法 | 路径 | 转发 |
|------|------|------|
| GET | `/billing/plans` | → billing |
| GET | `/billing/subscription` | → billing（注入 X-User-Id） |
| POST | `/billing/orders` | → billing（注入 X-User-Id） |
| POST | `/billing/orders/{id}/mock-confirm` | mock 支付确认（仅 dev/test；BFF 持密钥签名回调 webhook，密钥不出服务端；生产 404） |

### proxy 闸门行为

- 平台兜底分支：查当月 `quota_periods`（不存在按 free plan 惰性创建）
- `platform_llm_model ∉ plan.allowed_models` → 403 `model_not_allowed`
- `used_cents >= quota_cents` → 402 `quota_exceeded`（含 quota/used/plan 信息）
- 调用完成后按 `pricing.cost_cents` 原子累加 `used_cents`（流式在 finally 中执行）
- 用户自带 Key 通道完全不受影响

## 5. 移动端

- `core/network/api/billing_api.dart`：plans / subscription / orders 三个调用
- `features/billing/`：订阅状态卡片（套餐名、周期、额度进度条）+ 套餐页（三档卡片 + mock 购买按钮）
- 「我的」页加入口卡片
- mock 购买流：下单 → 本地「模拟支付成功」→ 调 webhook mock confirm（dev 环境按钮）

## 6. 验收标准

1. `pytest backend/billing` 全绿（套餐/下单幂等/激活同步 is_paid/惰性过期/webhook 验签与幂等/admin grant）
2. `pytest backend/proxy` 全绿（原有测试不回归 + 新增闸门用例：无额度 402 / 模型禁入 403 / 记账正确 / 自带 Key 通道不受影响）
3. `pytest backend/bff` 全绿（原有测试不回归 + billing 代理用例）
4. `docker compose config` 校验通过；nginx `nginx -t` 语义正确
5. 移动端 `flutter analyze` 无新增 error
