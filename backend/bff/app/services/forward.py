"""上游转发工具（业务路由 → Agent 编排服务）。"""

from __future__ import annotations

import httpx
from fastapi.responses import Response

from eduagent_shared.config import get_settings
from eduagent_shared.logging import get_logger

from ..errors import AppError

log = get_logger("bff.forward")


async def forward_to_agent(
    client: httpx.AsyncClient,
    path: str,
    *,
    user_id: str,
    method: str = "GET",
    params: dict | None = None,
    json_body: dict | None = None,
) -> Response:
    """把请求透传到 Agent 编排服务，附带服务间令牌与用户 ID。"""
    settings = get_settings()
    url = f"{settings.agent_orchestrator_url}{path}"
    headers = {
        "X-Internal-Token": settings.internal_service_token,
        "X-User-Id": user_id,
    }
    try:
        resp = await client.request(
            method, url, params=params, json=json_body, headers=headers, timeout=60.0
        )
    except httpx.HTTPError as exc:
        log.warning("agent_forward_error", path=path, error=str(exc))
        raise AppError(502, "upstream_unavailable", "业务服务暂不可用") from exc
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )
