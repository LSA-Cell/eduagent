"""模型列表路由：鉴权后透传推理网关 GET /v1/models（OpenAI 风格 {object, data}）。"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import Response

from eduagent_shared.config import get_settings
from eduagent_shared.logging import get_logger

from ..deps import get_current_user, get_http_client
from ..errors import AppError

router = APIRouter(tags=["models"])
log = get_logger("bff.models")


@router.get("/models")
async def list_models(
    user=Depends(get_current_user),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> Response:
    """可选模型列表（双轨：自有 vLLM / 用户 API Key），原样透传网关响应。"""
    settings = get_settings()
    url = f"{settings.inference_gateway_url}/v1/models"
    headers = {"X-Internal-Token": settings.internal_service_token}
    try:
        resp = await client.get(url, headers=headers, timeout=15.0)
    except httpx.HTTPError as exc:
        log.warning("gateway_models_error", error=str(exc))
        raise AppError(502, "gateway_unavailable", "推理网关暂不可用") from exc
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )
