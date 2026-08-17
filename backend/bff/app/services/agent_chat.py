"""聊天统一入口：BFF → Agent 编排服务（POST /v1/chat/stream）的转发与协议适配。

红线：
- BFF 聊天请求一律经 Agent 编排层（记忆/RAG/工具/护栏），禁止绕过编排
  直连推理网关；LLM 出口只在 Agent → Gateway 一段；
- 会话与消息写库由 Agent 统一负责，BFF 只做归属校验（读），不再写
  messages，消除双落库；
- 本模块只做协议翻译：Agent 统一 SSE 事件（run.start/message.delta/...
  见 docs/api/asyncapi.yaml）→ OpenAI 兼容 chunk，供未升级的旧客户端使用；
  新客户端应直接消费 ``POST /chat/stream`` 的原样事件流。
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from eduagent_shared.config import Settings
from eduagent_shared.logging import get_logger

log = get_logger("bff.agent_chat")

_AGENT_STREAM_PATH = "/v1/chat/stream"


# ---------------------------------------------------------------------------
# 请求构造
# ---------------------------------------------------------------------------
def last_user_text(messages: list[Any]) -> str:
    """取最后一条 user 消息的纯文本（多模态分段取 text 部分拼接）。"""
    for message in reversed(messages):
        role = getattr(message, "role", None) or (
            message.get("role") if isinstance(message, dict) else None
        )
        if role != "user":
            continue
        content = getattr(message, "content", None) if not isinstance(message, dict) else message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
    return ""


def build_agent_body(payload: Any, user: Any) -> dict[str, Any]:
    """ChatCompletionRequest + 当前用户 → Agent ChatStreamRequest 请求体。

    身份字段（role/grade_level/quota_tier）由 BFF 依据用户记录填写，
    客户端不可伪造；user_id 服务端注入。
    """
    is_teacher = bool(getattr(user, "is_teacher", False))
    is_paid = bool(getattr(user, "is_paid", False))
    body: dict[str, Any] = {
        "user_id": str(user.id),
        "message": last_user_text(payload.messages),
        "subject": payload.subject,
        "model": payload.model,
        "role": "teacher" if is_teacher else "student",
        "grade_level": getattr(user, "grade_level", None),
        "quota_tier": "teacher" if is_teacher else ("paid" if is_paid else "free"),
    }
    if payload.session_id is not None:
        body["session_id"] = str(payload.session_id)
    return body


def agent_headers(settings: Settings, user: Any) -> dict[str, str]:
    """服务间调用头：内部令牌 + 目标用户。"""
    return {
        "X-Internal-Token": settings.internal_service_token,
        "X-User-Id": str(user.id),
    }


async def open_agent_stream(
    client: httpx.AsyncClient,
    settings: Settings,
    body: dict[str, Any],
    user: Any,
    *,
    extra_headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    """打开到 Agent 的流式连接（调用方负责 aclose）。"""
    headers = {**agent_headers(settings, user), **(extra_headers or {})}
    request = client.build_request(
        "POST",
        f"{settings.agent_orchestrator_url}{_AGENT_STREAM_PATH}",
        json=body,
        headers=headers,
        params=params,
    )
    return await client.send(request, stream=True)


# ---------------------------------------------------------------------------
# SSE 帧解析（Agent 侧为 ``id:/event:/data:`` 三段式）
# ---------------------------------------------------------------------------
async def _iter_frames(upstream: AsyncIterator[bytes]) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """逐帧产出 (event_type, data_json)。解析失败的帧跳过（宽容策略）。"""
    buffer = ""
    async for chunk in upstream:
        buffer += chunk.decode("utf-8", errors="replace")
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            event_type = "message"
            data_lines: list[str] = []
            for line in frame.splitlines():
                if line.startswith("event:"):
                    event_type = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:"):].lstrip())
            if not data_lines:
                continue
            try:
                data = json.loads("\n".join(data_lines))
            except ValueError:
                continue
            yield event_type, data


# ---------------------------------------------------------------------------
# 协议翻译：Agent 事件 → OpenAI 兼容 chunk（旧客户端契约）
# ---------------------------------------------------------------------------
def _chunk(chunk_id: str, model: str, delta: dict[str, Any], finish_reason: str | None = None) -> bytes:
    payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


_DONE = b"data: [DONE]\n\n"

# run.end finish_reason → OpenAI finish_reason
_FINISH_MAP = {
    "stop": "stop",
    "max_turns": "stop",
    "token_budget": "length",
    "max_continuations": "length",
    "timeout": "stop",
    "error": "stop",
    "circuit_broken": "stop",
}


async def translate_to_openai_chunks(
    upstream: AsyncIterator[bytes], *, model: str
) -> AsyncIterator[bytes]:
    """Agent 9 类事件流 → OpenAI chat.completion.chunk 流。

    翻译规则：run.start → role chunk；message.delta → 文本 chunk；
    error → 文本 chunk（错误信息可读透出，绝不静默吞错）；run.end →
    finish chunk + [DONE]；tool/card/guardrail.ask 等富事件旧客户端
    无法渲染，跳过（新客户端应改用 /chat/stream 原样事件流）。
    """
    chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
    async for event_type, data in _iter_frames(upstream):
        if event_type == "run.start":
            yield _chunk(chunk_id, model, {"role": "assistant"})
        elif event_type == "message.delta":
            text = str(data.get("text", ""))
            if text:
                yield _chunk(chunk_id, model, {"content": text})
        elif event_type == "error":
            message = str(data.get("message", "服务暂不可用"))
            yield _chunk(chunk_id, model, {"content": f"⚠️ {message}"})
        elif event_type == "run.end":
            finish = _FINISH_MAP.get(str(data.get("finish_reason", "stop")), "stop")
            yield _chunk(chunk_id, model, {}, finish)
            yield _DONE
            return
    # 上游未发 run.end 即断流：补结束帧，避免客户端悬挂
    yield _chunk(chunk_id, model, {}, "stop")
    yield _DONE


async def collect_completion(
    upstream: AsyncIterator[bytes], *, model: str
) -> dict[str, Any]:
    """非流式聚合：Agent 事件流 → OpenAI chat.completion JSON。"""
    parts: list[str] = []
    finish_reason = "stop"
    async for event_type, data in _iter_frames(upstream):
        if event_type == "message.delta":
            parts.append(str(data.get("text", "")))
        elif event_type == "error":
            parts.append(f"⚠️ {data.get('message', '服务暂不可用')}")
        elif event_type == "run.end":
            finish_reason = _FINISH_MAP.get(str(data.get("finish_reason", "stop")), "stop")
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "".join(parts)},
                "finish_reason": finish_reason,
            }
        ],
    }
