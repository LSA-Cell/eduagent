"""DreamTask Celery 任务（§4.2）：按用户粒度、retry ≤2、低优先级队列 "dream"。

- ``agent.dream.dispatch``：beat 每日 02:00 触发（celery_app.beat_schedule），
  只处理近 24h 有活动的用户，逐用户投递 ``agent.dream.run_user`` 到 dream
  队列（分批 countdown 错峰，禁止挤占白天服务资源）；
- ``agent.dream.run_user``：单用户四阶段整合，失败重试 ≤2 次（max_retries=2），
  完成后 fail-soft 触发 dream.completed 自动化 Hook（§5.4）。

红线：整合任务只存在于 Celery 异步路径，同步请求路径无任何调用点。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select

from eduagent_shared import db
from eduagent_shared.logging import get_logger

from app.tasks.celery_app import celery_app, run_async

log = get_logger("agent.tasks.dream")

DREAM_QUEUE = "dream"  # 低优先级队列（与默认 agent 队列隔离）
DISPATCH_BATCH_SIZE = 50  # 每批用户数
DISPATCH_BATCH_INTERVAL_S = 60  # 批间错峰（02:00 起分批）
MAX_RETRIES = 2  # 红线：失败重试 ≤2 次


async def find_active_users(
    session: Any, *, since: Optional[datetime] = None
) -> list[str]:
    """近 24h 有活动的用户（chat_sessions created_at/updated_at 命中窗口）。"""
    since = since or (datetime.now(timezone.utc) - timedelta(hours=24))
    from eduagent_shared.models import ChatSession

    rows = (await session.execute(select(ChatSession))).scalars().all()
    user_ids: set[str] = set()
    for chat_session in rows:
        for attr in ("created_at", "updated_at"):  # Python 侧复核（FakeSession 兼容）
            moment = getattr(chat_session, attr, None)
            if moment is None:
                continue
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            if moment >= since:
                user_ids.add(str(chat_session.user_id))
                break
    return sorted(user_ids)  # 确定性顺序


async def dispatch_dream_batch(
    *,
    session_factory: Any = None,
    sender: Optional[Callable[..., Any]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """beat 入口核心：找活跃用户 → 分批投递单用户整合任务（dream 队列）。"""
    factory = session_factory or db.get_session_factory()
    now = now or datetime.now(timezone.utc)
    day = (now - timedelta(hours=3)).date()  # 02:00 跑批整理"刚刚结束的那天"
    async with factory() as session:
        user_ids = await find_active_users(session, since=now - timedelta(hours=24))

    if sender is None:
        def sender(task_name: str, payload: dict[str, Any], *, countdown: int = 0) -> None:
            celery_app.send_task(
                task_name, kwargs=payload, queue=DREAM_QUEUE, countdown=countdown
            )

    for index, user_id in enumerate(user_ids):
        countdown = (index // DISPATCH_BATCH_SIZE) * DISPATCH_BATCH_INTERVAL_S
        sender(
            "agent.dream.run_user",
            {"user_id": user_id, "day": day.isoformat()},
            countdown=countdown,
        )
    log.info("dream_dispatched", users=len(user_ids), day=day.isoformat())
    return {"dispatched": len(user_ids), "day": day.isoformat()}


async def run_dream_for_user(
    user_id: str,
    *,
    day: Optional[str] = None,
    retry_count: int = 0,
    session_factory: Any = None,
    llm: Any = None,
    lock: Any = None,
    pruner: Any = None,
    hook_dispatcher: Any = None,  # 测试注入；None 时按需加载真实 dispatcher
) -> dict[str, Any]:
    """单用户整合核心（测试直接调用；Celery 入口见下方 task）。"""
    from eduagent_shared.config import get_settings

    from app.dream.locks import RedisDreamLock
    from app.dream.pipeline import DreamPipeline

    factory = session_factory or db.get_session_factory()
    if llm is None:
        from app.services.llm_client import get_llm_client

        llm = get_llm_client(get_settings())
    if lock is None:
        from eduagent_shared.redis_client import get_redis

        lock = RedisDreamLock(get_redis())
    activity_day = date.fromisoformat(day) if day else None

    pipeline = DreamPipeline(factory, lock=lock, llm=llm, pruner=pruner)
    result = await pipeline.run(user_id, day=activity_day, retry_count=retry_count)

    if result.get("status") == "completed":
        # §5.4：dream.completed 自动化 Hook（fail-soft，异步派发，不影响整合结果）
        try:
            if hook_dispatcher is None:
                from app.hooks.dispatcher import dispatch_event

                hook_dispatcher = dispatch_event
            await hook_dispatcher(
                "dream.completed",
                {"user_id": user_id, "day": result["day"], "stats": result["stats"]},
                session_factory=factory,
            )
        except Exception:  # noqa: BLE001 - Hook 失败不影响主流程
            log.warning("dream_completed_hook_error", exc_info=True)
    return result


@celery_app.task(name="agent.dream.dispatch")
def dream_dispatch() -> dict[str, Any]:
    """每日 02:00 beat 入口（Celery）。"""
    return run_async(dispatch_dream_batch())


@celery_app.task(
    name="agent.dream.run_user",
    bind=True,
    max_retries=MAX_RETRIES,
    queue=DREAM_QUEUE,
    retry_backoff=True,
)
def dream_run_user(self: Any, user_id: str, day: Optional[str] = None) -> dict[str, Any]:
    """单用户夜间整合（Celery 入口）；失败重试 ≤2 次，超出后任务失败可监控。"""
    try:
        return run_async(
            run_dream_for_user(user_id, day=day, retry_count=self.request.retries)
        )
    except Exception as exc:  # noqa: BLE001 - 统一走 Celery retry（≤2 次）
        log.warning(
            "dream_run_user_failed",
            user_id=user_id,
            retry=self.request.retries,
            error=str(exc),
        )
        raise self.retry(exc=exc, countdown=300 * (2 ** self.request.retries)) from exc
