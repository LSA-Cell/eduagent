"""第三方大模型厂商适配：请求构造、响应 → OpenAI 格式转换、SSE 流转换。

统一契约：
- 非流式：上游响应一律转换为 OpenAI chat.completion JSON（含 usage）；
- 流式：上游 SSE 事件一律转换为 OpenAI chat.completion.chunk，
  由路由层在末尾补 usage chunk（converter.usage_chunk()）与 ``data: [DONE]``。

安全红线：api_key 仅作为局部变量在请求构造时注入上游 HTTP 头，
永不写日志、永不出现在任何响应体。
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from ..errors import ProxyError
from ..schemas import ChatMessage

# ---------- 厂商端点 ----------
OPENAI_BASE_URL = "https://api.openai.com"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
CLAUDE_BASE_URL = "https://api.anthropic.com"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"

ANTHROPIC_VERSION = "2023-06-01"
CLAUDE_DEFAULT_MAX_TOKENS = 1024  # Anthropic messages API 必填 max_tokens

# claude stop_reason → OpenAI finish_reason
_CLAUDE_FINISH = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
}
# gemini finishReason → OpenAI finish_reason
_GEMINI_FINISH = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
}


def message_text(content: Any) -> str:
    """提取消息文本：str 原样；多模态分段数组仅提取 text 段拼接。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return "" if content is None else str(content)


def _usage(prompt_tokens: int, completion_tokens: int) -> dict[str, int]:
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _openai_chunk(
    chunk_id: str,
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def _new_chunk_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex}"


async def iter_sse(resp: httpx.Response) -> AsyncIterator[tuple[str | None, str]]:
    """解析 SSE 流：event/data 行按空行分帧，兼容多行 data 与冒号注释心跳。"""
    event: str | None = None
    data_lines: list[str] = []
    async for line in resp.aiter_lines():
        if not line:
            if data_lines:
                yield event, "\n".join(data_lines)
            event, data_lines = None, []
            continue
        if line.startswith(":"):  # 注释 / 心跳
            continue
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
    if data_lines:
        yield event, "\n".join(data_lines)


@dataclass
class ProviderRequest:
    url: str
    headers: dict[str, str]
    body: dict[str, Any]


class StreamConverter(Protocol):
    """SSE 事件 → OpenAI chunk；feed 返回 (待下发 chunk 列表, 是否流结束)。"""

    usage: dict | None

    def feed(self, event: str | None, data: str) -> tuple[list[dict], bool]: ...
    def usage_chunk(self) -> dict | None:
        """末尾待补发的 usage chunk（None 表示流内已透传，无需补发）。"""
        ...


class ProviderAdapter(Protocol):
    def build_request(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        stream: bool,
        max_tokens: int | None,
        api_key: str,
    ) -> ProviderRequest: ...
    def convert_completion(self, data: dict, *, model: str) -> dict: ...
    def new_stream_converter(self, *, model: str) -> StreamConverter: ...


# ---------------------------------------------------------------------------
# OpenAI 兼容厂商（openai / deepseek）：请求与响应原样透传，仅注入鉴权头
# ---------------------------------------------------------------------------


class _OpenAIStreamConverter:
    """透传 chunk（解析后重组），同时捕获末个 usage chunk 与 [DONE]。"""

    def __init__(self) -> None:
        self.usage: dict | None = None

    def feed(self, event: str | None, data: str) -> tuple[list[dict], bool]:
        if data.strip() == "[DONE]":
            return [], True
        try:
            chunk = json.loads(data)
        except ValueError:
            return [], False
        if chunk.get("usage"):
            self.usage = chunk["usage"]
        return [chunk], False

    def usage_chunk(self) -> dict | None:
        return None  # usage chunk 已随流透传，无需补发


class OpenAICompatAdapter:
    def __init__(self, base_url: str, path: str) -> None:
        self._url = base_url + path

    def build_request(
        self, *, model, messages, stream, max_tokens, api_key
    ) -> ProviderRequest:
        body: dict[str, Any] = {
            "model": model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "stream": stream,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if stream:
            # 要求上游在末个 chunk 返回 usage，用于额度追踪
            body["stream_options"] = {"include_usage": True}
        return ProviderRequest(
            url=self._url,
            headers={"Authorization": f"Bearer {api_key}"},
            body=body,
        )

    def convert_completion(self, data: dict, *, model: str) -> dict:
        return data  # 已是 OpenAI 格式

    def new_stream_converter(self, *, model: str) -> StreamConverter:
        return _OpenAIStreamConverter()


# ---------------------------------------------------------------------------
# Anthropic Claude
# ---------------------------------------------------------------------------


class _ClaudeStreamConverter:
    """Anthropic SSE → OpenAI chunk：

    message_start → role chunk（捕获 input_tokens）；
    content_block_delta(text_delta) → content chunk；
    message_delta → finish chunk（捕获 stop_reason / output_tokens）；
    message_stop → 流结束（usage 由路由层经 usage_chunk() 补发）。
    """

    def __init__(self, *, model: str) -> None:
        self._model = model
        self._chunk_id = _new_chunk_id()
        self._input_tokens = 0
        self._output_tokens = 0
        self.usage: dict | None = None

    def feed(self, event: str | None, data: str) -> tuple[list[dict], bool]:
        try:
            payload = json.loads(data)
        except ValueError:
            return [], False
        if event == "message_start":
            usage = (payload.get("message") or {}).get("usage") or {}
            self._input_tokens = int(usage.get("input_tokens") or 0)
            chunk = _openai_chunk(self._chunk_id, self._model, {"role": "assistant"})
            return [chunk], False
        if event == "content_block_delta":
            delta = payload.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                chunk = _openai_chunk(
                    self._chunk_id, self._model, {"content": delta["text"]}
                )
                return [chunk], False
            return [], False
        if event == "message_delta":
            delta = payload.get("delta") or {}
            usage = payload.get("usage") or {}
            if usage.get("output_tokens") is not None:
                self._output_tokens = int(usage["output_tokens"])
            self.usage = _usage(self._input_tokens, self._output_tokens)
            finish = _CLAUDE_FINISH.get(delta.get("stop_reason") or "", "stop")
            chunk = _openai_chunk(self._chunk_id, self._model, {}, finish_reason=finish)
            return [chunk], False
        if event == "message_stop":
            return [], True
        return [], False  # content_block_start / ping 等事件无对应 chunk

    def usage_chunk(self) -> dict | None:
        if self.usage is None:
            return None
        chunk = _openai_chunk(self._chunk_id, self._model, {})
        chunk["choices"] = []
        chunk["usage"] = self.usage
        return chunk


class ClaudeAdapter:
    def build_request(
        self, *, model, messages, stream, max_tokens, api_key
    ) -> ProviderRequest:
        system_parts: list[str] = []
        claude_messages: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(message_text(m.content))
            else:
                role = "assistant" if m.role == "assistant" else "user"
                claude_messages.append({"role": role, "content": message_text(m.content)})
        body: dict[str, Any] = {
            "model": model,
            "messages": claude_messages,
            "max_tokens": max_tokens or CLAUDE_DEFAULT_MAX_TOKENS,
            "stream": stream,
        }
        if system_parts:
            body["system"] = "\n".join(system_parts)
        return ProviderRequest(
            url=f"{CLAUDE_BASE_URL}/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION},
            body=body,
        )

    def convert_completion(self, data: dict, *, model: str) -> dict:
        text = "".join(
            block.get("text", "")
            for block in data.get("content") or []
            if block.get("type") == "text"
        )
        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("output_tokens") or 0)
        return {
            "id": data.get("id") or _new_chunk_id(),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": _CLAUDE_FINISH.get(data.get("stop_reason") or "", "stop"),
                }
            ],
            "usage": _usage(prompt_tokens, completion_tokens),
        }

    def new_stream_converter(self, *, model: str) -> StreamConverter:
        return _ClaudeStreamConverter(model=model)


# ---------------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------------


class _GeminiStreamConverter:
    """Gemini streamGenerateContent SSE → OpenAI chunk（无显式结束事件，连接耗尽即止）。"""

    def __init__(self, *, model: str) -> None:
        self._model = model
        self._chunk_id = _new_chunk_id()
        self._role_sent = False
        self.usage: dict | None = None

    def feed(self, event: str | None, data: str) -> tuple[list[dict], bool]:
        try:
            payload = json.loads(data)
        except ValueError:
            return [], False
        chunks: list[dict] = []
        if not self._role_sent:
            self._role_sent = True
            chunks.append(_openai_chunk(self._chunk_id, self._model, {"role": "assistant"}))
        candidate = (payload.get("candidates") or [{}])[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
        if text:
            chunks.append(_openai_chunk(self._chunk_id, self._model, {"content": text}))
        finish = candidate.get("finishReason")
        if finish:
            chunks.append(
                _openai_chunk(
                    self._chunk_id, self._model, {},
                    finish_reason=_GEMINI_FINISH.get(finish, "stop"),
                )
            )
        meta = payload.get("usageMetadata")
        if meta:
            self.usage = _usage(
                int(meta.get("promptTokenCount") or 0),
                int(meta.get("candidatesTokenCount") or 0),
            )
        return chunks, False

    def usage_chunk(self) -> dict | None:
        if self.usage is None:
            return None
        chunk = _openai_chunk(self._chunk_id, self._model, {})
        chunk["choices"] = []
        chunk["usage"] = self.usage
        return chunk


class GeminiAdapter:
    def build_request(
        self, *, model, messages, stream, max_tokens, api_key
    ) -> ProviderRequest:
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(message_text(m.content))
            else:
                role = "model" if m.role == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": message_text(m.content)}]})
        body: dict[str, Any] = {"contents": contents}
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
        if max_tokens is not None:
            body["generationConfig"] = {"maxOutputTokens": max_tokens}
        action = "streamGenerateContent" if stream else "generateContent"
        url = f"{GEMINI_BASE_URL}/v1beta/models/{model}:{action}"
        if stream:
            url += "?alt=sse"
        return ProviderRequest(url=url, headers={"x-goog-api-key": api_key}, body=body)

    def convert_completion(self, data: dict, *, model: str) -> dict:
        candidate = (data.get("candidates") or [{}])[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
        meta = data.get("usageMetadata") or {}
        prompt_tokens = int(meta.get("promptTokenCount") or 0)
        completion_tokens = int(meta.get("candidatesTokenCount") or 0)
        return {
            "id": _new_chunk_id(),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": _GEMINI_FINISH.get(
                        candidate.get("finishReason") or "", "stop"
                    ),
                }
            ],
            "usage": _usage(prompt_tokens, completion_tokens),
        }

    def new_stream_converter(self, *, model: str) -> StreamConverter:
        return _GeminiStreamConverter(model=model)


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

_ADAPTERS: dict[str, ProviderAdapter] = {
    "openai": OpenAICompatAdapter(OPENAI_BASE_URL, "/v1/chat/completions"),
    "deepseek": OpenAICompatAdapter(DEEPSEEK_BASE_URL, "/chat/completions"),
    "claude": ClaudeAdapter(),
    "gemini": GeminiAdapter(),
}


def get_adapter(provider: str) -> ProviderAdapter:
    try:
        return _ADAPTERS[provider]
    except KeyError:
        raise ProxyError(
            400,
            f"不支持的 provider: {provider}",
            "invalid_request_error",
            "unsupported_provider",
        ) from None
