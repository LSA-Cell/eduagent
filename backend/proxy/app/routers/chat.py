"""POST /proxy/chat：解密用户 Key → 厂商适配 → 统一 OpenAI 格式回传 → 额度记账。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse

from eduagent_shared.config import get_settings
from eduagent_shared.logging import get_logger
from eduagent_shared.security.crypto import DecryptError, decrypt_key

from ..deps import (
    get_http_client,
    get_key_store,
    get_quota_store,
    require_internal_token,
)
from ..errors import ProxyError
from ..schemas import ProxyChatRequest
from ..services.pricing import cost_cents, estimate_tokens
from ..services.providers import (
    ProviderAdapter,
    ProviderRequest,
    get_adapter,
    iter_sse,
    message_text,
)
from ..services.quota import (
    QuotaContext,
    QuotaStore,
    charge_platform_quota,
    check_platform_quota,
)
from ..services.store import ApiKeyStore

router = APIRouter(prefix="/proxy", tags=["proxy"], dependencies=[Depends(require_internal_token)])
log = get_logger("proxy.chat")


def _map_upstream_error(resp: httpx.Response, body: bytes, api_key: str) -> ProxyError:
    """上游非 2xx：4xx 原样透传状态码，5xx 归一为 502。

    安全红线：上游错误消息可能回显 Key（如 401），透传前必须脱敏。
    """
    message = f"上游厂商返回错误（HTTP {resp.status_code}）"
    try:
        data = json.loads(body)
        err = data.get("error")
        if isinstance(err, dict) and err.get("message"):
            message = str(err["message"])
        elif data.get("message"):
            message = str(data["message"])
    except ValueError:
        pass
    message = message.replace(api_key, "***")
    status = resp.status_code if resp.status_code < 500 else 502
    return ProxyError(status, message, "upstream_error", "upstream_error")


async def _charge(
    store: ApiKeyStore,
    *,
    key_id: Any,
    provider: str,
    model: str,
    usage: dict | None,
    prompt_text: str,
    completion_text: str,
) -> None:
    """按 usage（缺失时按字符估算）折算美分并累加 used_budget；记账失败仅记日志，不影响已下发响应。"""
    try:
        if usage:
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
        else:
            prompt_tokens = estimate_tokens(prompt_text)
            completion_tokens = estimate_tokens(completion_text)
        cents = cost_cents(provider, model, prompt_tokens, completion_tokens)
        if cents <= 0:
            return
        new_used = await store.add_used_budget(key_id, cents)
        log.info(
            "proxy_charged",
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_cents=cents,
            used_budget=new_used,
        )
    except Exception:
        log.exception("proxy_charge_failed", provider=provider, model=model)


async def _charge_platform(
    quota_store: QuotaStore,
    qctx: QuotaContext,
    *,
    provider: str,
    model: str,
    usage: dict | None,
    prompt_text: str,
    completion_text: str,
) -> None:
    """平台兜底通道：按实际成本扣减用户月度额度（Billing v1 软闸门后置记账）。"""
    try:
        if usage:
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
        else:
            prompt_tokens = estimate_tokens(prompt_text)
            completion_tokens = estimate_tokens(completion_text)
        cents = cost_cents(provider, model, prompt_tokens, completion_tokens)
        await charge_platform_quota(quota_store, qctx, cents)
    except Exception:
        log.exception("platform_quota_charge_failed", provider=provider, model=model)


@router.post("/chat")
async def proxy_chat(
    payload: ProxyChatRequest,
    store: ApiKeyStore = Depends(get_key_store),
    quota_store: QuotaStore = Depends(get_quota_store),
    client: httpx.AsyncClient = Depends(get_http_client),
):
    record = await store.get_active(payload.user_id, payload.provider)
    settings = get_settings()
    qctx: QuotaContext | None = None

    if record is None and settings.platform_llm_provider:
        # 平台级兜底：用户未绑定自有 Key 时，由平台统一 Key 代付转发。
        # Billing v1：转发前过会员额度闸门（模型准入 + 月度额度），调用后按实际成本扣额度。
        provider = settings.platform_llm_provider
        model = settings.platform_llm_model
        api_key = settings.platform_llm_api_key
        qctx = await check_platform_quota(quota_store, payload.user_id, model)
        log.info(
            "proxy_platform_fallback",
            user_id=str(payload.user_id),
            provider=provider,
            model=model,
            plan_id=qctx.plan_id,
        )
    elif record is None:
        raise ProxyError(
            404,
            "未找到该服务商的可用 API Key，请先在 App 中绑定",
            "invalid_request_error",
            "api_key_not_found",
        )
    else:
        provider = payload.provider
        model = payload.model
        if record.monthly_budget is not None and record.used_budget >= record.monthly_budget:
            raise ProxyError(
                402,
                "该服务商月度额度已用尽，请调整预算或更换推理通道",
                "budget_exceeded",
                "monthly_budget_exceeded",
            )

        # 安全红线：明文 Key 只存在于本作用域局部变量，不落库、不写日志、不进响应体
        try:
            api_key = decrypt_key(record.encrypted_key)
        except DecryptError:
            log.error("api_key_decrypt_failed", provider=provider, key_id=str(record.id))
            raise ProxyError(
                500, "API Key 解密失败，请重新绑定", "server_error", "key_decrypt_failed"
            ) from None

    adapter = get_adapter(provider)
    request = adapter.build_request(
        model=model,
        messages=payload.messages,
        stream=payload.stream,
        max_tokens=payload.max_tokens,
        api_key=api_key,
    )
    log.info(
        "proxy_forward",
        provider=provider,
        model=model,
        stream=payload.stream,
    )

    if payload.stream:
        return await _open_stream(
            request, adapter, payload, store, client, record, api_key,
            provider=provider, model=model,
            quota_store=quota_store, qctx=qctx,
        )
    return await _call(
        request, adapter, payload, store, client, record, api_key,
        provider=provider, model=model,
        quota_store=quota_store, qctx=qctx,
    )


async def _call(
    request: ProviderRequest,
    adapter: ProviderAdapter,
    payload: ProxyChatRequest,
    store: ApiKeyStore,
    client: httpx.AsyncClient,
    record: Any | None,
    api_key: str,
    *,
    provider: str,
    model: str,
    quota_store: QuotaStore,
    qctx: QuotaContext | None,
) -> JSONResponse:
    """非流式：上游 JSON → OpenAI chat.completion。"""
    try:
        resp = await client.post(request.url, json=request.body, headers=request.headers)
    except httpx.HTTPError as exc:
        log.warning("upstream_connect_failed", provider=provider, error=str(exc))
        raise ProxyError(
            502, "上游厂商连接失败", "upstream_error", "upstream_unreachable"
        ) from exc
    if resp.status_code != 200:
        raise _map_upstream_error(resp, resp.content, api_key)
    try:
        data = resp.json()
    except ValueError:
        raise ProxyError(
            502, "上游响应解析失败", "upstream_error", "invalid_upstream_response"
        ) from None
    completion = adapter.convert_completion(data, model=model)
    completion_text = ""
    choices = completion.get("choices") or []
    if choices:
        completion_text = (choices[0].get("message") or {}).get("content") or ""
    if record is not None:  # 用户 Key 通道：记 key 行额度
        await _charge(
            store,
            key_id=record.id,
            provider=provider,
            model=model,
            usage=completion.get("usage"),
            prompt_text="".join(message_text(m.content) for m in payload.messages),
            completion_text=completion_text,
        )
    elif qctx is not None:  # 平台兜底通道：扣会员月度额度
        await _charge_platform(
            quota_store,
            qctx,
            provider=provider,
            model=model,
            usage=completion.get("usage"),
            prompt_text="".join(message_text(m.content) for m in payload.messages),
            completion_text=completion_text,
        )
    return JSONResponse(completion)


async def _open_stream(
    request: ProviderRequest,
    adapter: ProviderAdapter,
    payload: ProxyChatRequest,
    store: ApiKeyStore,
    client: httpx.AsyncClient,
    record: Any | None,
    api_key: str,
    *,
    provider: str,
    model: str,
    quota_store: QuotaStore,
    qctx: QuotaContext | None,
) -> StreamingResponse:
    """流式：上游 SSE → 统一 OpenAI chunk，末尾补 usage chunk 与 [DONE]。"""
    http_request = client.build_request(
        "POST", request.url, json=request.body, headers=request.headers
    )
    try:
        resp = await client.send(http_request, stream=True)
    except httpx.HTTPError as exc:
        log.warning("upstream_connect_failed", provider=provider, error=str(exc))
        raise ProxyError(
            502, "上游厂商连接失败", "upstream_error", "upstream_unreachable"
        ) from exc
    if resp.status_code != 200:
        body = await resp.aread()
        await resp.aclose()
        raise _map_upstream_error(resp, body, api_key)

    converter = adapter.new_stream_converter(model=model)
    prompt_text = "".join(message_text(m.content) for m in payload.messages)

    async def gen() -> AsyncIterator[bytes]:
        collected: list[str] = []
        try:
            try:
                async for event, data in iter_sse(resp):
                    chunks, done = converter.feed(event, data)
                    for chunk in chunks:
                        delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                        if isinstance(delta.get("content"), str):
                            collected.append(delta["content"])
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
                    if done:
                        break
            except httpx.HTTPError:
                # 流中途失败：内容已部分下发，优雅收尾（计费仍在 finally 执行）
                log.warning("upstream_stream_broken", provider=provider)
            usage_chunk = converter.usage_chunk()
            if usage_chunk is not None:
                yield f"data: {json.dumps(usage_chunk, ensure_ascii=False)}\n\n".encode()
            yield b"data: [DONE]\n\n"
        finally:
            await resp.aclose()
            if record is not None:  # 用户 Key 通道：记 key 行额度
                await _charge(
                    store,
                    key_id=record.id,
                    provider=provider,
                    model=model,
                    usage=converter.usage,
                    prompt_text=prompt_text,
                    completion_text="".join(collected),
                )
            elif qctx is not None:  # 平台兜底通道：扣会员月度额度
                await _charge_platform(
                    quota_store,
                    qctx,
                    provider=provider,
                    model=model,
                    usage=converter.usage,
                    prompt_text=prompt_text,
                    completion_text="".join(collected),
                )

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
