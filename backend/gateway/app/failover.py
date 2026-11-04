"""FailoverRouter：双轨路由 + 降级策略（网关核心）。

路由决策：
1. inference_source=internal → 仅自有 vLLM；失败直接报错，不降级；
2. inference_source=external → 仅 external（用户自备 Key 通道）；
3. auto（默认）→ 优先自有模型；连接失败 / 首 token 超时 / 实例熔断时，
   若 FAILOVER_ENABLED 且请求 auto_failover=true 且带 user_id，降级 external 重试；
   自有模型无路由时 auto 也会落到 external。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from eduagent_shared.config import Settings
from eduagent_shared.logging import get_logger

from .errors import GatewayError
from .health import HealthRegistry
from .schemas import ChatCompletionRequest

log = get_logger("gateway.failover")

DEFAULT_MODEL = "edu-general-14b"
FIRST_TOKEN_TIMEOUT_SECONDS = 5.0  # 首 token 超时阈值

# 学科 → 自有模型映射（运维可按部署实际调整；目标模型不在 VLLM_ROUTES 时自动忽略）
SUBJECT_MODEL_MAP: dict[str, str] = {
    "math": "edu-math-7b",
    "physics": "edu-math-7b",
    "chemistry": "edu-math-7b",
    "biology": "edu-general-14b",
    "general": "edu-general-14b",
}


class _InstanceError(Exception):
    """单个 vLLM 实例调用失败（可熔断/可换实例重试）。"""


@dataclass
class UpstreamResult:
    """上游调用结果：非流式（json）或流式（stream）。"""

    kind: Literal["json", "stream"]
    status: int
    source: str  # internal:<instance> / external
    content: bytes | None = None
    media_type: str = "application/json"
    stream_gen: AsyncIterator[bytes] | None = None


class FailoverRouter:
    """按健康度在 vLLM 实例间路由，失败时按策略降级到 external。"""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        registry: HealthRegistry,
        settings: Settings,
        subject_map: dict[str, str] | None = None,
        first_token_timeout: float = FIRST_TOKEN_TIMEOUT_SECONDS,
        request_timeout: float = 60.0,
        default_model: str = DEFAULT_MODEL,
    ) -> None:
        self.client = client
        self.registry = registry
        self.settings = settings
        self.subject_map = subject_map if subject_map is not None else SUBJECT_MODEL_MAP
        self.first_token_timeout = first_token_timeout
        self.request_timeout = request_timeout
        self.default_model = default_model

    # ------------------------------------------------------------------
    # 路由决策
    # ------------------------------------------------------------------

    def resolve_model(self, req: ChatCompletionRequest) -> str:
        if req.inference_source == "external" and req.model:
            return req.model

        if req.subject:
            mapped = self.subject_map.get(req.subject)
            if mapped and mapped in self.settings.vllm_routes:
                return mapped
        return req.model or self.default_model

    async def chat(self, req: ChatCompletionRequest) -> UpstreamResult:
        errors: list[str] = []
        model = self.resolve_model(req)

        if req.inference_source in ("auto", "internal"):
            instances = self.settings.vllm_routes.get(model) or []
            if instances:
                result = await self._try_internal(req, model, instances, errors)
                if result is not None:
                    return result
            else:
                errors.append(f"internal: 模型 {model} 无自有实例路由")

        failover_allowed = self.settings.failover_enabled and req.auto_failover
        if req.inference_source == "external" or (
            req.inference_source == "auto" and failover_allowed
        ):
            if not req.user_id:
                errors.append("external: 缺少 user_id（需用户配置 API Key）")
            else:
                result = await self._try_external(req, model, errors)
                if result is not None:
                    return result

        detail = "; ".join(errors) if errors else "无可用推理通道"
        raise GatewayError(502, f"所有推理通道暂不可用: {detail}", "server_error")

    # ------------------------------------------------------------------
    # internal：自有 vLLM
    # ------------------------------------------------------------------

    async def _try_internal(
        self,
        req: ChatCompletionRequest,
        model: str,
        instances: list[str],
        errors: list[str],
    ) -> UpstreamResult | None:
        candidates = await self.registry.candidates(model, instances)
        if not candidates:
            errors.append(f"internal: 模型 {model} 实例全部熔断中")
            return None

        body = req.to_openai_body(model)
        for instance in candidates:
            try:
                if req.stream:
                    result = await self._open_internal_stream(instance, body)
                else:
                    result = await self._call_internal(instance, body)
            except _InstanceError as exc:
                errors.append(f"internal:{instance}: {exc}")
                await self.registry.mark_failure(instance)
                continue
            await self.registry.mark_success(instance)
            return UpstreamResult(
                result.kind,
                result.status,
                source=f"internal:{instance}",
                content=result.content,
                media_type=result.media_type,
                stream_gen=result.stream_gen,
            )
        return None

    async def _call_internal(self, instance: str, body: dict[str, Any]) -> UpstreamResult:
        """非流式调用；连接失败/5xx 视为实例故障，4xx 原样透传。"""
        url = f"{instance}/v1/chat/completions"
        try:
            resp = await self.client.post(url, json=body, timeout=self.request_timeout)
        except httpx.HTTPError as exc:
            raise _InstanceError(f"连接失败: {exc}") from exc
        if resp.status_code >= 500:
            raise _InstanceError(f"上游状态码 {resp.status_code}")
        return UpstreamResult(
            "json",
            resp.status_code,
            source=f"internal:{instance}",
            content=resp.content,
            media_type=resp.headers.get("content-type", "application/json"),
        )

    async def _open_internal_stream(
        self, instance: str, body: dict[str, Any]
    ) -> UpstreamResult:
        """打开流式调用并等待首个 chunk（超时视为实例故障）。"""
        url = f"{instance}/v1/chat/completions"
        request = self.client.build_request("POST", url, json=body)
        try:
            resp = await self.client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise _InstanceError(f"连接失败: {exc}") from exc

        if resp.status_code >= 500:
            await resp.aclose()
            raise _InstanceError(f"上游状态码 {resp.status_code}")
        if resp.status_code != 200:  # 4xx：读完原样透传
            content = await resp.aread()
            await resp.aclose()
            return UpstreamResult(
                "json",
                resp.status_code,
                source=f"internal:{instance}",
                content=content,
                media_type=resp.headers.get("content-type", "application/json"),
            )

        aiter = resp.aiter_raw()
        try:
            first = await asyncio.wait_for(
                aiter.__anext__(), timeout=self.first_token_timeout
            )
        except asyncio.TimeoutError:
            await resp.aclose()
            raise _InstanceError(
                f"首 token 超时（>{self.first_token_timeout}s）"
            ) from None
        except (StopAsyncIteration, httpx.HTTPError) as exc:
            await resp.aclose()
            raise _InstanceError(f"流启动失败: {exc}") from exc

        registry = self.registry

        async def gen() -> AsyncIterator[bytes]:
            yield first
            try:
                async for chunk in aiter:
                    yield chunk
            except httpx.HTTPError:
                # 流中途失败：首 token 已下发，无法降级重试，仅熔断记录后优雅收尾
                await registry.mark_failure(instance)
            finally:
                await resp.aclose()

        return UpstreamResult(
            "stream", 200, source=f"internal:{instance}",
            media_type="text/event-stream", stream_gen=gen(),
        )

    # ------------------------------------------------------------------
    # external：用户自备 API Key 通道（经 API 代理服务）
    # ------------------------------------------------------------------

    async def _try_external(
        self, req: ChatCompletionRequest, model: str, errors: list[str]
    ) -> UpstreamResult | None:
        settings = self.settings
        url = f"{settings.api_proxy_url}/proxy/chat"
        body: dict[str, Any] = {
            "user_id": req.user_id,
            "provider": req.provider or "openai",
            "model": model,
            "messages": [m.model_dump(exclude_none=True) for m in req.messages],
            "stream": req.stream,
        }
        if req.max_tokens is not None:
            body["max_tokens"] = req.max_tokens
        headers = {"X-Internal-Token": settings.internal_service_token}

        try:
            if not req.stream:
                resp = await self.client.post(
                    url, json=body, headers=headers, timeout=self.request_timeout
                )
                if resp.status_code >= 500:
                    errors.append(f"external: 代理状态码 {resp.status_code}")
                    return None
                # 4xx（无 Key/超预算等）原样透传给调用方
                return UpstreamResult(
                    "json",
                    resp.status_code,
                    source="external",
                    content=resp.content,
                    media_type=resp.headers.get("content-type", "application/json"),
                )

            resp = await self.client.send(
                self.client.build_request("POST", url, json=body, headers=headers),
                stream=True,
            )
        except httpx.HTTPError as exc:
            errors.append(f"external: 连接失败: {exc}")
            return None

        if resp.status_code >= 500:
            await resp.aclose()
            errors.append(f"external: 代理状态码 {resp.status_code}")
            return None
        if resp.status_code != 200:
            content = await resp.aread()
            await resp.aclose()
            return UpstreamResult(
                "json",
                resp.status_code,
                source="external",
                content=content,
                media_type=resp.headers.get("content-type", "application/json"),
            )

        async def gen() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            finally:
                await resp.aclose()

        return UpstreamResult(
            "stream", 200, source="external",
            media_type="text/event-stream", stream_gen=gen(),
        )
