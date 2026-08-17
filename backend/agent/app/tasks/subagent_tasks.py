"""通用子 Agent Celery 任务：async_via=celery 且非内容生成类的子 Agent 在此执行。

当前承载 grading_agent（作业批改）：worker 进程内跑同步子 Loop，
完成后把结果以 assistant 消息落库（前端经消息列表/重连重放获取）。
幂等红线：以 ``complete:{task_id}`` 去重，重复完成事件/重复投递不重复写库。
"""

from __future__ import annotations

from typing import Any, Optional

from eduagent_shared import db
from eduagent_shared.logging import get_logger

from app.subagents.celery_bridge import TaskDedup
from app.subagents.loader import load_registry
from app.subagents.runner import run_sync_subagent
from app.tasks.celery_app import celery_app, run_async
from app.tools.base import ToolContext

log = get_logger("agent.tasks.subagent")


async def run_subagent_async(
    task_id: str,
    agent: str,
    user_id: str,
    task: str = "",
    params: Optional[dict[str, Any]] = None,
    subject: Optional[str] = None,
    session_id: Optional[str] = None,
    *,
    session_factory: Any = None,
    deps: Any = None,
    dedup: Any = None,
) -> dict[str, Any]:
    """worker 侧执行体（依赖可注入，离线可测）。

    返回 {"task_id", "agent", "status": completed/duplicate/failed/not_found}。
    """
    dedup = dedup or TaskDedup()
    if not await dedup.claim(f"complete:{task_id}"):
        log.info("subagent_duplicate_completion", task_id=task_id, agent=agent)
        return {"task_id": task_id, "agent": agent, "status": "duplicate"}

    registry = load_registry()
    definition = registry.maybe_get(agent)
    if definition is None:
        return {"task_id": task_id, "agent": agent, "status": "not_found"}

    if deps is None:
        from app.loop.deps_protocol import DefaultDeps

        deps = DefaultDeps()
    ctx = ToolContext(
        user_id=user_id, session_id=session_id, deps=deps,
        subject=subject, depth=1,  # worker 视角为顶层；子 Loop 实际 depth=2
    )
    result = await run_sync_subagent(
        definition, task=task, params=params, subject=subject,
        deps=deps, parent_ctx=ctx, depth=2,
    )

    # 结果落库：assistant 消息（卡片数据进 metadata）；落库失败不影响任务结果
    if session_id:
        try:
            from app.services import chat_service

            factory = session_factory or db.get_session_factory()
            async with factory() as session:
                await chat_service.save_message(
                    session, session_id, "assistant", result.content,
                    metadata={
                        "task_id": task_id,
                        "agent": agent,
                        "card_type": result.card_type,
                        "card_data": result.card_data,
                    },
                )
                await session.commit()
        except Exception:  # noqa: BLE001 - 写库失败仅记日志（结果可由重试补偿）
            log.warning("subagent_result_persist_failed", task_id=task_id, agent=agent)

    status = "completed"
    if isinstance(result.card_data, dict) and result.card_data.get("status") in {
        "failed", "timeout",
    }:
        status = "failed"
    log.info("subagent_finished", task_id=task_id, agent=agent, status=status)
    return {"task_id": task_id, "agent": agent, "status": status}


@celery_app.task(name="agent.subagent.run")
def run_subagent(
    task_id: str,
    agent: str,
    user_id: str,
    task: str = "",
    params: Optional[dict[str, Any]] = None,
    subject: Optional[str] = None,
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    """通用子 Agent 执行（Celery 入口）。"""
    return run_async(
        run_subagent_async(
            task_id, agent, user_id, task=task,
            params=params, subject=subject, session_id=session_id,
        )
    )
