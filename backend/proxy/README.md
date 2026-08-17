# EduAgent 用户 API Key 代理（端口 8004）

接收推理网关的 external 通道请求，解密用户自备的第三方大模型 Key 后按厂商适配转发，
统一以 OpenAI 格式回传，并按内置价格表折算额度（USD 美分）累加 `used_budget`。

## 接口

- `POST /proxy/chat`（需 `X-Internal-Token` 头）：
  入参 `{user_id, provider, model, messages, stream, max_tokens}`；
  `provider ∈ openai / deepseek / claude / gemini`。
  - openai/deepseek：OpenAI 兼容 `/chat/completions`（deepseek base_url 为
    `https://api.deepseek.com`），请求/响应原样透传（流式时注入
    `stream_options.include_usage` 以追踪用量）；
  - claude：`POST https://api.anthropic.com/v1/messages`（`x-api-key` +
    `anthropic-version` 头），SSE 事件 `message_start / content_block_delta /
    message_delta` 转换为 OpenAI `chat.completion.chunk`；
  - gemini：`generateContent / streamGenerateContent?alt=sse`（`x-goog-api-key` 头），
    同样转换为 OpenAI chunk；
  - 流式统一以 OpenAI SSE chunk 回传，末尾补 usage chunk 与 `data: [DONE]`。
- `GET /proxy/usage/{user_id}`：各 provider 用量（预算/已用/剩余，**不含任何 Key**）。
- `GET /health`。

## 额度与安全

- 价格表为 `app/services/pricing.py` 中 `PRICE_TABLE` 常量（USD / 1M tokens，
  型号按最长前缀匹配）；`usage` 缺失时按约 4 字符 / token 估算；
  `monthly_budget` 为 NULL 表示不限额，超限返回 402；记账为原子累加 DB 写。
- 安全红线：Key 解密后仅存内存局部变量、永不写日志（接入 shared logging 脱敏）、
  永不出现在任何响应体（上游错误消息透传前也会做 Key 脱敏）。

## 本地启动与测试

```bash
# 在 backend/ 目录下
pip install -e shared -r proxy/requirements.txt
cd proxy && uvicorn app.main:app --reload --port 8004

pip install -r requirements-dev.txt   # 在 proxy/ 内
python -m pytest                      # 全部离线可过（MockTransport + InMemory 仓储）
```

> 说明：本服务未做成 pip 包，测试通过 `proxy/conftest.py` 把 `proxy/` 与 `shared/`
> 加入 `sys.path` 后直接 `import app`；若已 `pip install -e shared` 则共用环境安装。

## Docker

由根目录 docker-compose 构建（build context 为 `./backend`，dockerfile 为 `proxy/Dockerfile`）。
