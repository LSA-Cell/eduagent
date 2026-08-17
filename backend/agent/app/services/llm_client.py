"""推理网关客户端。

所有 LLM 调用必须经推理网关（OpenAI 兼容 + SSE），禁止直连 vLLM 或第三方。
网关地址来自 settings.INFERENCE_GATEWAY_URL，鉴权使用内部服务令牌。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

import httpx

from eduagent_shared.config import Settings

DEFAULT_MODEL = "edu-general-14b"  # 网关路由表中的默认模型名


@dataclass
class ToolCall:
    """OpenAI 格式的单次工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResult:
    """非流式补全结果。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""


class LLMError(RuntimeError):
    """网关调用失败或响应格式异常。"""


class PromptTooLongError(LLMError):
    """网关判定上下文超长（HTTP 413 / prompt_too_long / context_length）。

    Loop 收到后走响应式压缩重试（L4）。
    """


def _is_prompt_too_long(status: int, body: str) -> bool:
    if status == 413:
        return True
    lowered = body.lower()
    return "prompt_too_long" in lowered or "context_length" in lowered or "maximum context" in lowered


def extract_json(text: str) -> Any:
    """从 LLM 输出中提取 JSON（容忍 ```json 代码围栏与首尾杂文本）。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # 去掉首行围栏与结尾围栏
        lines = cleaned.splitlines()
        lines = lines[1:] if lines else []
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # 退化策略：截取第一个 {/[ 与最后一个 }/] 之间的内容
    start_candidates = [i for i in (cleaned.find("{"), cleaned.find("[")) if i >= 0]
    end_candidates = [i for i in (cleaned.rfind("}"), cleaned.rfind("]")) if i >= 0]
    if start_candidates and end_candidates:
        start, end = min(start_candidates), max(end_candidates) + 1
        try:
            return json.loads(cleaned[start:end])
        except json.JSONDecodeError:
            pass
    raise LLMError(f"无法从 LLM 输出解析 JSON: {text[:200]}")


class LLMClient:
    """推理网关 httpx 封装：chat / chat_stream / embeddings。"""

    def __init__(self, base_url: str, token: str, timeout: float = 120.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "X-Internal-Token": token,
            "Content-Type": "application/json",
        }
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url, headers=self._headers, timeout=self._timeout
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> ChatResult:
        """非流式补全；tools 非空时启用工具调用。"""
        payload: dict[str, Any] = {
            "model": model or DEFAULT_MODEL,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if max_tokens:
            payload["max_tokens"] = max_tokens
        async with self._client() as client:
            resp = await client.post("/v1/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"网关响应缺少 choices: {str(data)[:200]}") from exc
        tool_calls = [
            ToolCall(
                id=str(tc.get("id", f"call_{i}")),
                name=tc["function"]["name"],
                arguments=_parse_arguments(tc["function"].get("arguments", "{}")),
            )
            for i, tc in enumerate(message.get("tool_calls") or [])
        ]
        return ChatResult(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            finish_reason=data["choices"][0].get("finish_reason", ""),
        )

    async def chat_json(
        self,
        system: str,
        user: str,
        *,
        model: Optional[str] = None,
        temperature: float = 0.3,
    ) -> Any:
        """要求 JSON 输出的便捷封装（调用方负责二次校验）。"""
        result = await self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=model,
            temperature=temperature,
        )
        return extract_json(result.content)

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """SSE 流式补全，逐段产出文本增量。"""
        payload = {
            "model": model or DEFAULT_MODEL,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        async with self._client() as client:
            async with client.stream("POST", "/v1/chat/completions", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta") or {}
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    content = delta.get("content")
                    if content:
                        yield content

    async def chat_stream_events(
        self,
        messages: list[dict[str, Any]],
        *,
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """SSE 流式补全（支持工具调用收集），产出事件 dict：

        - ``{"text": ...}``：文本增量（打字机）；
        - 最后一帧 ``{"finish_reason": ..., "tool_calls": [ToolCall, ...]}``。

        上下文超长时抛 PromptTooLongError（供 Loop 走响应式压缩）。
        """
        payload: dict[str, Any] = {
            "model": model or DEFAULT_MODEL,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if max_tokens:
            payload["max_tokens"] = max_tokens
        tool_acc: dict[int, dict[str, Any]] = {}
        finish_reason = ""
        async with self._client() as client:
            async with client.stream("POST", "/v1/chat/completions", json=payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                    if _is_prompt_too_long(resp.status_code, body):
                        raise PromptTooLongError(f"网关判定上下文超长: {body[:200]}")
                    raise LLMError(f"网关流式请求失败({resp.status_code}): {body[:200]}")
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        choice = chunk["choices"][0]
                        delta = choice.get("delta") or {}
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if _is_prompt_too_long(200, json.dumps(chunk.get("error") or {})):
                        raise PromptTooLongError("网关流式响应判定上下文超长")
                    content = delta.get("content")
                    if content:
                        yield {"text": content}
                    for tc in delta.get("tool_calls") or []:
                        idx = int(tc.get("index", 0))
                        acc = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            acc["id"] = tc["id"]
                        function = tc.get("function") or {}
                        if function.get("name"):
                            acc["name"] = function["name"]
                        if function.get("arguments"):
                            acc["arguments"] += function["arguments"]
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
        yield {
            "finish_reason": finish_reason or "stop",
            "tool_calls": [
                ToolCall(
                    id=tool_acc[i]["id"] or f"call_{i}",
                    name=tool_acc[i]["name"],
                    arguments=_parse_arguments(tool_acc[i]["arguments"]),
                )
                for i in sorted(tool_acc)
                if tool_acc[i]["name"]
            ],
        }

    async def embed(self, texts: list[str], *, model: Optional[str] = None) -> list[list[float]]:
        """调网关 /v1/embeddings 生成向量。"""
        payload: dict[str, Any] = {"input": texts}
        if model:
            payload["model"] = model
        async with self._client() as client:
            resp = await client.post("/v1/embeddings", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return [item["embedding"] for item in data["data"]]


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """OpenAI tool_calls 的 arguments 是 JSON 字符串，做容错解析。"""
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def get_llm_client(settings: Settings) -> LLMClient:
    """按配置构造网关客户端（任务/服务统一入口，便于测试替换）。"""
    return LLMClient(
        base_url=settings.inference_gateway_url,
        token=settings.internal_service_token,
    )
