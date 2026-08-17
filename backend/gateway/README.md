# EduAgent 推理网关（端口 8001）

OpenAI 兼容的推理入口，**只做路由与稳定性保障，不含任何业务逻辑**（不批改/不做计划/不生成 PPT）。

## 双轨路由

- `internal`：按 `VLLM_ROUTES`（env JSON：模型 → vLLM 实例列表）路由；
  学科路由：`subject=math/physics/chemistry` 时优先 `SUBJECT_MODEL_MAP` 中的学科模型
  （代码内置，运维可改 `app/failover.py` 顶部常量）。
- `external`：转发 `API_PROXY_URL/proxy/chat`（用户自备 API Key 通道），SSE 透传。

## 负载均衡 / 熔断 / 降级

- Redis 记录实例健康（`MODEL_HEALTH_TTL_SECONDS`），健康实例按 round-robin 选择；
- 实例连续失败 3 次（`FAILURE_THRESHOLD`）标记 unhealthy，TTL 到期自动半开试探；
- 自有模型连接失败 / 5s 无首 token → 若 `FAILOVER_ENABLED` 且请求 `auto_failover=true`
  且带 `user_id`（即用户配置了 API Key），自动切 external 重试；
- 全部失败返回标准 OpenAI 错误格式 `{"error": {message, type, code}}`。

## 接口

- `POST /v1/chat/completions`（需 `X-Internal-Token` 头，OpenAI 兼容 + 扩展字段
  `subject` / `inference_source` / `user_id` / `provider` / `auto_failover`）
- `GET /v1/models`（模型卡片由 `model_cards.json` 驱动）
- `GET /healthz`

## 本地启动与测试

```bash
# 在 backend/ 目录下
pip install -e shared -r gateway/requirements.txt
cd gateway && uvicorn app.main:app --reload --port 8001

pip install -r requirements-dev.txt   # 在 gateway/ 内
python -m pytest                      # 全部离线可过（MockTransport + fakeredis）
```

> 说明：external 通道的 `model` 需为对应厂商认识的模型名（如 `deepseek-chat`）；
> 自有模型名（`edu-*`）仅适用于 internal 通道。
