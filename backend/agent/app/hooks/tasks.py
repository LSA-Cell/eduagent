"""自动化 Hook 动作执行（§5.4）：Celery worker 侧，占位通知动作。

首批动作 notify_parent / notify_teacher 经注入的通知发送接口（Notifier
协议）执行；默认 LoggingNotifier 仅记日志（通知通道未接入前的占位实现，
接入时替换为 BFF/推送通道客户端，不允许在动作内引入同步请求路径调用）。
"""

from __future__ import annotations

from typing import Any, Optional

from eduagent_shared.logging import get_logger

from app.hooks.registry import ACTIONS
from app.tasks.celery_app import celery_app, run_async

log = get_logger("agent.tasks.hooks")

_MAX_PAYLOAD_STR = 120  # 通知载荷字符串截断（通知只带元信息，禁止原文外溢）


def _sanitize(value: Any) -> Any:
    """载荷卫生化：长字符串截断，防止消息原文进入通知通道。"""
    if isinstance(value, str):
        return value if len(value) <= _MAX_PAYLOAD_STR else value[:_MAX_PAYLOAD_STR] + "…"
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    return value


class LoggingNotifier:
    """占位通知发送接口：仅结构化日志（真实通道接入时替换）。"""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(
        self,
        *,
        action: str,
        event: str,
        owner_id: Optional[str],
        config: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        record = {
            "action": action, "event": event, "owner_id": owner_id,
            "config": dict(config), "payload": dict(payload),
        }
        self.sent.append(record)
        log.info("hook_notify", **record)


async def run_hook_action(
    *,
    action: str,
    event: str,
    payload: Optional[dict[str, Any]] = None,
    owner_id: Optional[str] = None,
    config: Optional[dict[str, Any]] = None,
    notifier: Any = None,
) -> dict[str, Any]:
    """执行单个 Hook 动作（测试直接调用；Celery 入口见下方 task）。"""
    if action not in ACTIONS:
        log.warning("hook_unknown_action", action=action, event=event)
        return {"ok": False, "reason": "unknown_action", "action": action}
    notifier = notifier if notifier is not None else LoggingNotifier()
    await notifier.send(
        action=action,
        event=event,
        owner_id=owner_id,
        config=_sanitize(config or {}),
        payload=_sanitize(payload or {}),
    )
    return {"ok": True, "action": action, "event": event}


@celery_app.task(name="agent.hook.execute", max_retries=0)
def execute_hook(
    hook_id: Optional[str] = None,
    event: Optional[str] = None,
    action: Optional[str] = None,
    owner_id: Optional[str] = None,
    config: Optional[dict[str, Any]] = None,
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Hook 动作异步执行（Celery 入口）。

    通知失败不重试（max_retries=0）：错过一条家长/教师通知可接受，
    重试风暴打挂通知通道不可接受；失败仅留日志与任务失败状态。
    """
    return run_async(
        run_hook_action(
            action=str(action or ""), event=str(event or ""),
            owner_id=owner_id, config=config, payload=payload,
        )
    )
