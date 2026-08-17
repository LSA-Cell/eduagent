"""路由：/v1/chat/completions 与 /v1/models。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse

from eduagent_shared.logging import get_logger

from .deps import get_failover_router, require_internal_token
from .failover import FailoverRouter
from .schemas import ChatCompletionRequest

router = APIRouter()
log = get_logger("gateway.routes")


@router.post("/v1/chat/completions", dependencies=[Depends(require_internal_token)])
async def chat_completions(
    payload: ChatCompletionRequest,
    failover_router: FailoverRouter = Depends(get_failover_router),
):
    """OpenAI 兼容聊天入口：internal（vLLM）/ external（用户 Key）双轨。"""
    result = await failover_router.chat(payload)
    log.info(
        "chat_routed",
        source=result.source,
        status=result.status,
        stream=payload.stream,
        model=payload.model,
    )
    if result.kind == "stream":
        return StreamingResponse(
            result.stream_gen,
            status_code=result.status,
            media_type=result.media_type,
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return Response(
        content=result.content,
        status_code=result.status,
        media_type=result.media_type,
    )


@router.get("/v1/models")
async def list_models(request: Request) -> dict:
    """返回可用模型卡片（配置文件 model_cards.json 驱动）。"""
    return {"object": "list", "data": request.app.state.model_cards}
