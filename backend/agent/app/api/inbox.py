"""运行中消息端点（增强机制 §2.3）：追问入队 / 停止。

- POST /v1/chat/messages：运行中追加消息，默认 later 优先级（流式 turn 收尾后
  steer 消费）；队列上限 20 条，超出拒绝（429）；
- POST /v1/chat/stop：停止当前会话运行——next 类控制消息，任何时刻立即处理，
  真实取消贯穿到工具任务与模型流（禁止假取消）。

消息经注入的 inbox（InMemory/Redis）送达运行中的 Loop；无活动运行时
消息仍会入箱，由下一次该会话的 run 在 turn 边界消费。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from app import deps as app_deps
from app.queue.message_queue import IncomingMessage
from app.schemas import ChatStopRequest, InboxMessageRequest

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/chat",
    tags=["orchestration-inbox"],
    dependencies=[Depends(app_deps.require_internal_auth)],
)


@router.post("/messages")
async def enqueue_message(
    req: InboxMessageRequest,
    x_user_id: str = Header(default="", alias="X-User-Id"),
    orch_deps: Any = Depends(app_deps.get_orchestration_deps),
) -> dict[str, Any]:
    """追加消息进运行中队列（later 普通追问 / next 控制类）。"""
    inbox = getattr(orch_deps, "inbox", None)
    if inbox is None:
        raise HTTPException(status_code=503, detail="消息通道不可用")
    msg = IncomingMessage(content=req.message, kind="text", priority=req.priority)
    await inbox.push(req.session_id, msg)
    return {"status": "queued", "message_id": msg.message_id, "priority": msg.priority}


@router.post("/stop")
async def stop_run(
    req: ChatStopRequest,
    x_user_id: str = Header(default="", alias="X-User-Id"),
    orch_deps: Any = Depends(app_deps.get_orchestration_deps),
) -> dict[str, Any]:
    """停止运行（next 类控制消息；Loop 决策点立即处理，不被普通排队阻塞）。"""
    inbox = getattr(orch_deps, "inbox", None)
    if inbox is None:
        raise HTTPException(status_code=503, detail="消息通道不可用")
    msg = IncomingMessage(kind="stop", priority="next")
    await inbox.push(req.session_id, msg)
    return {"status": "stopping", "message_id": msg.message_id}
