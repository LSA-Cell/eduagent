"""dream.summary 次日首启摘要组装（§4.2.4）。

推送路径：次日用户首次进入会话（edu_agent_loop 启动）时，经
``deps.dream_summary_provider`` 取出最近一次未投递的 completed 整合结果，
组装 dream.summary 事件数据（{stats}，仅计数）并以 card_type=dream_summary
卡片呈现。红线：推送内容必须经内容审核（deps.moderate，fail-closed）；
stats 只含计数，禁止携带消息内容/用户原文。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select

from app.dream.models import DreamRun

logger = logging.getLogger(__name__)

SUMMARY_LOOKBACK_HOURS = 36  # 首启推送回看窗口（跨天+时区冗余）

# AsyncAPI DreamSummary.stats 封闭键集（additionalProperties: false）
STATS_KEYS = ("mistakes_organized", "cards_created", "memories_updated")


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def render_summary_text(stats: dict[str, Any]) -> str:
    """摘要文案（客户端卡片也可直接按 stats 渲染，此文本仅用于过审与兜底展示）。"""
    mistakes = int(stats.get("mistakes_organized", 0) or 0)
    cards = int(stats.get("cards_created", 0) or 0)
    memories = int(stats.get("memories_updated", 0) or 0)
    if mistakes == 0 and cards == 0 and memories == 0:
        return "已完成昨夜的学习整理，今天也要加油。"
    return (
        f"已为你整理昨日 {mistakes} 道错题，"
        f"生成 {cards} 张复习卡，更新 {memories} 条学习记忆。"
    )


def extract_stats(run_stats: dict[str, Any]) -> dict[str, int]:
    """从 dream_runs.stats 提取协议内计数键（协议外键一律丢弃）。"""
    return {key: int((run_stats or {}).get(key, 0) or 0) for key in STATS_KEYS}


async def build_dream_summary(
    session: Any,
    user_id: str,
    *,
    moderate: Optional[Callable[[str], Awaitable[bool]]] = None,
    now: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    """取最近一次未投递的 completed 整合结果，过审后标记投递并返回摘要。

    返回 {"run_id", "stats", "text"}；无可推送结果或审核未通过返回 None。
    审核异常 fail-closed（不推送，但 delivered_at 也不标记，下次首启重试）。
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=SUMMARY_LOOKBACK_HOURS)
    uid = _as_uuid(user_id)
    rows = (
        await session.execute(
            select(DreamRun)
            .where(
                DreamRun.user_id == uid,
                DreamRun.phase == "completed",
                DreamRun.delivered_at.is_(None),
                DreamRun.finished_at >= cutoff,
            )
            .order_by(DreamRun.finished_at.desc())
        )
    ).scalars().all()
    # Python 侧复核（FakeSession 忽略 WHERE，测试自证）
    candidates = []
    for row in rows:
        if str(getattr(row, "user_id", "")) != str(uid):
            continue
        if getattr(row, "phase", None) != "completed" or getattr(row, "delivered_at", None):
            continue
        finished = getattr(row, "finished_at", None)
        if finished is not None:
            if finished.tzinfo is None:
                finished = finished.replace(tzinfo=timezone.utc)
            if finished < cutoff:
                continue
        candidates.append(row)
    if not candidates:
        return None
    run = max(candidates, key=lambda r: r.finished_at or r.started_at or now)

    stats = extract_stats(getattr(run, "stats", None) or {})
    text = render_summary_text(stats)
    if moderate is not None:
        try:
            safe = await moderate(text)
        except Exception:  # noqa: BLE001 - 审核故障 fail-closed：本次不推送
            logger.warning("dream.summary 内容审核失败，本次不推送", exc_info=True)
            return None
        if not safe:
            logger.warning("dream.summary 内容审核未通过，丢弃本次推送")
            run.delivered_at = now  # 内容不过审：标记已处理，避免每轮会话重复审核
            await session.flush()
            return None
    run.delivered_at = now
    await session.flush()
    return {"run_id": str(run.id), "stats": stats, "text": text}


def make_summary_provider(
    session_factory: Callable[[], Any],
    *,
    moderate: Optional[Callable[[str], Awaitable[bool]]] = None,
) -> Callable[[str], Awaitable[Optional[dict[str, Any]]]]:
    """组装 deps.dream_summary_provider（每次调用独立会话，fail-soft 由调用方兜底）。"""

    async def provider(user_id: str) -> Optional[dict[str, Any]]:
        async with session_factory() as session:
            summary = await build_dream_summary(session, user_id, moderate=moderate)
            await session.commit()
            return summary

    return provider


__all__ = [
    "STATS_KEYS",
    "SUMMARY_LOOKBACK_HOURS",
    "build_dream_summary",
    "extract_stats",
    "make_summary_provider",
    "render_summary_text",
]
