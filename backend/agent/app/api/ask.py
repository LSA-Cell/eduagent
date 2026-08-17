"""POST /v1/ask/answers：AskUserQuestion 结构化提问的答复回传（增强机制 §2.2）。

闭环：Loop 挂起在 ask_store.wait（TTL 120s，期间无模型调用）；客户端渲染
ask 事件卡片后把 {ask_id, answers} 提交到本端点；resolve 唤醒挂起的 Loop
注入答复继续循环。超时未答复由 Loop 按超时分级策略处理（普通→推荐项默认
继续并明示；敏感决策→按取消）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from app import deps as app_deps
from app.schemas import AskAnswerRequest

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1",
    tags=["orchestration-ask"],
    dependencies=[Depends(app_deps.require_internal_auth)],
)


@router.post("/ask/answers")
async def submit_ask_answers(
    req: AskAnswerRequest,
    x_user_id: str = Header(default="", alias="X-User-Id"),
    orch_deps: Any = Depends(app_deps.get_orchestration_deps),
) -> dict[str, str]:
    """回传 ask 答复并唤醒挂起的 Loop（无挂起/过期/跨用户 → 410）。"""
    store = getattr(orch_deps, "ask_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="ask 通道不可用")
    ok = await store.resolve(req.ask_id, req.answers, user_id=x_user_id or None)
    if not ok:
        raise HTTPException(status_code=410, detail="提问已过期或不存在")
    return {"status": "resolved", "ask_id": req.ask_id}
