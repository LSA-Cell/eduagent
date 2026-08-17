"""自动化 Hook 事件派发（§5.4）：事件触发 → Celery 异步执行，fail-soft。

红线：Hook 动作禁止出现在同步请求路径——本模块只 send_task 投递，
动作执行全部发生在 worker（app/hooks/tasks.py）；任何异常仅记日志，
主流程结果不受影响。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from app.hooks.registry import HookRegistry

logger = logging.getLogger(__name__)

HOOK_TASK_NAME = "agent.hook.execute"


def _default_sender() -> Callable[[dict[str, Any]], None]:
    from app.hooks.tasks import execute_hook

    def send(kwargs: dict[str, Any]) -> None:
        # apply_async 走 task_always_eager（测试 eager 同步执行）；
        # send_task 绕过 eager 语义，禁用（Celery AlwaysEagerIgnored）
        execute_hook.apply_async(kwargs=kwargs)

    return send


async def dispatch_event(
    event: str,
    payload: dict[str, Any],
    *,
    session_factory: Any = None,
    registry: Optional[HookRegistry] = None,
    sender: Optional[Callable[[dict[str, Any]], Any]] = None,
) -> dict[str, Any]:
    """派发一个业务事件：查 automation_hooks 配置 → 逐条投递 Celery 任务。

    全程 fail-soft：DB 故障/无配置/投递异常都返回 dispatched 计数而不抛出。
    payload 只应携带元信息（id/计数/状态），禁止携带用户消息原文。
    """
    try:
        if registry is None:
            if session_factory is None:
                from eduagent_shared import db

                session_factory = db.get_session_factory()
            registry = HookRegistry(session_factory)
        if registry.stale:
            await registry.refresh()
        hooks = registry.hooks_for(event)
        if not hooks:
            return {"event": event, "dispatched": 0}
        send = sender or _default_sender()
        dispatched = 0
        for hook in hooks:
            try:
                send({
                    "hook_id": hook["hook_id"],
                    "event": event,
                    "action": hook["action"],
                    "owner_id": hook.get("owner_id"),
                    "config": hook.get("config") or {},
                    "payload": dict(payload),
                })
                dispatched += 1
            except Exception:  # noqa: BLE001 - 单条投递失败不影响其余
                logger.warning("hook 任务投递失败: %s/%s", event, hook.get("action"), exc_info=True)
        return {"event": event, "dispatched": dispatched}
    except Exception:  # noqa: BLE001 - 红线：Hook 失败不影响主流程
        logger.warning("hook 事件派发失败（fail-soft）: %s", event, exc_info=True)
        return {"event": event, "dispatched": 0, "error": True}


__all__ = ["HOOK_TASK_NAME", "dispatch_event"]
