"""DreamTask 四阶段固定流水线（§4.2.2）：orient → gather → consolidate → prune。

- orient：盘点当日会话与工具产物（只读统计）；
- gather：错题聚类、知识点归并（只读，聚类结果进 stats）；
- consolidate：更新知识点掌握度矩阵（派生数据进 stats）、生成复习卡
  （front 前缀"【AI 生成】"，红线 §4.2.2）、沉淀长期记忆（weakness 去重写库）；
- prune：清理过期数据（dream_runs 历史保留 90 天；临时文件由注入 pruner
  扩展，MinIO 生命周期策略兜底）。

红线（结构性强制）：
- 未持锁禁止执行整合——锁获取失败直接返回 skipped，四个阶段根本不会进入；
- 全程只 session.add 派生对象（DreamRun/Flashcard/Memory），
  禁止 UPDATE messages/mistake_records 的原始内容列；
- 单用户整合目标 ≤60s（§八.5）：卡片生成一次小模型调用、上限 5 张。
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Optional

from pydantic import ValidationError
from sqlalchemy import select

from eduagent_shared.models import ChatSession, Flashcard, Message, MistakeRecord

from app import prompts
from app.audit.models import AgentRun
from app.dream.locks import InMemoryDreamLock
from app.dream.models import DreamRun
from app.memory import memory_manager
from app.services.analytics_service import compute_knowledge_mastery
from app.services.content_gen import FlashcardSpec

logger = logging.getLogger(__name__)

PHASES = ("orient", "gather", "consolidate", "prune")  # 固定流水线顺序，禁止调换
AI_GENERATED_MARK = "【AI 生成】"  # 红线：自动生成的复习卡必须显式标注
MAX_DREAM_CARDS = 5  # 单夜单用户卡片上限（控制成本与打扰度）
MAX_CONSOLIDATE_TAGS = 3  # 掌握度矩阵最薄弱的 3 个知识点进入整合
DREAM_HISTORY_RETENTION_DAYS = 90
_MASTERY_MATRIX_TOP = 5


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    """活动日期的 [起, 止)（UTC 简化口径）。"""
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _in_window(dt: Any, start: datetime, end: datetime) -> bool:
    """datetime 落在 [start, end) 内（naive 按 UTC 计；FakeSession 兼容复核用）。"""
    if dt is None:
        return False
    if isinstance(dt, datetime) and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return start <= dt < end


async def default_pruner(session: Any, user_id: str, *, now: datetime) -> dict[str, int]:
    """默认 prune：清理过期 dream_runs 历史（>90 天）。

    缓存与临时文件：Redis 键靠 TTL 自然过期、MinIO 临时前缀由生命周期
    策略清理（infra 侧职责）；需要应用侧主动清理时经 pipeline 的 pruner
    注入点扩展，禁止在本函数引入新的外部依赖。
    """
    cutoff = now - timedelta(days=DREAM_HISTORY_RETENTION_DAYS)
    rows = await session.execute(
        select(DreamRun).where(
            DreamRun.user_id == _as_uuid(user_id), DreamRun.started_at < cutoff
        )
    )
    pruned = 0
    for row in rows.scalars().all():
        started = row.started_at
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if started is None or started >= cutoff:  # Python 侧复核（FakeSession 兼容）
            continue
        await session.delete(row)
        pruned += 1
    return {"dream_history_pruned": pruned, "temp_files_removed": 0}


class DreamPipeline:
    """四阶段流水线（全部 I/O 经注入接口：session_factory/lock/llm/pruner）。"""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        lock: Any = None,
        llm: Any = None,  # None 时跳过卡片生成（cards_created=0）
        pruner: Any = None,  # async (session, user_id, now=...) -> dict
    ) -> None:
        self._session_factory = session_factory
        self._lock = lock if lock is not None else InMemoryDreamLock()
        self._llm = llm
        self._pruner = pruner if pruner is not None else default_pruner

    async def run(
        self,
        user_id: str,
        *,
        day: Optional[date] = None,
        retry_count: int = 0,
    ) -> dict[str, Any]:
        """执行当日整合；锁冲突跳过（禁止并发双跑），阶段失败置 failed 并抛出。"""
        day = day or (datetime.now(timezone.utc).date() - timedelta(days=1))
        acquired = await self._lock.acquire(user_id, day)
        if not acquired:
            # 红线：锁冲突即跳过该用户当日整合
            logger.info("dream 锁冲突，跳过用户当日整合: %s %s", user_id, day)
            return {"status": "skipped", "reason": "lock_conflict",
                    "user_id": user_id, "day": day.isoformat()}
        stats: dict[str, Any] = {}
        try:
            async with self._session_factory() as session:
                run_row = DreamRun(
                    user_id=_as_uuid(user_id), phase=PHASES[0], stats={},
                    retry_count=int(retry_count),
                )
                session.add(run_row)
                await session.flush()
                ctx: dict[str, Any] = {}
                try:
                    for phase in PHASES:  # 固定顺序，逐阶段产物落 dream_runs.stats
                        stage_stats = await self._run_phase(
                            phase, session, user_id, day, ctx
                        )
                        stats.update(stage_stats)
                        run_row.phase = phase
                        run_row.stats = dict(stats)
                        await session.flush()
                    run_row.phase = "completed"
                    run_row.finished_at = datetime.now(timezone.utc)
                    run_row.stats = dict(stats)
                    await session.commit()
                except Exception:
                    run_row.phase = "failed"
                    run_row.finished_at = datetime.now(timezone.utc)
                    run_row.stats = dict(stats)
                    try:
                        await session.commit()
                    except Exception:  # noqa: BLE001 - 失败状态落库尽力而为
                        logger.warning("dream_runs 失败状态落库失败", exc_info=True)
                    raise
            logger.info("dream_completed", user_id=user_id, day=day.isoformat(), stats=stats)
            return {
                "status": "completed", "user_id": user_id, "day": day.isoformat(),
                "dream_run_id": str(run_row.id), "stats": stats,
            }
        finally:
            await self._lock.release(user_id, day)

    # ------------------------------------------------------------------
    # 四阶段实现
    # ------------------------------------------------------------------
    async def _run_phase(
        self, phase: str, session: Any, user_id: str, day: date, ctx: dict[str, Any]
    ) -> dict[str, Any]:
        if phase == "orient":
            return await self._orient(session, user_id, day, ctx)
        if phase == "gather":
            return await self._gather(session, user_id, day, ctx)
        if phase == "consolidate":
            return await self._consolidate(session, user_id, day, ctx)
        if phase == "prune":
            return await self._prune(session, user_id)
        raise ValueError(f"未知 dream 阶段: {phase!r}")

    async def _orient(
        self, session: Any, user_id: str, day: date, ctx: dict[str, Any]
    ) -> dict[str, Any]:
        """盘点当日会话与工具产物（只读统计，不取消息内容进 stats）。"""
        start, end = _day_bounds(day)
        uid = _as_uuid(user_id)

        sessions = (
            await session.execute(select(ChatSession).where(ChatSession.user_id == uid))
        ).scalars().all()
        session_ids = {
            str(s.id) for s in sessions if str(s.user_id) == str(uid)  # Python 侧复核
        }

        messages = (await session.execute(select(Message))).scalars().all()
        day_messages = [
            m for m in messages
            if str(getattr(m, "session_id", "")) in session_ids
            and _in_window(getattr(m, "created_at", None), start, end)
        ]

        runs = (
            await session.execute(select(AgentRun).where(AgentRun.user_id == uid))
        ).scalars().all()
        day_runs = [
            r for r in runs
            if str(getattr(r, "user_id", "")) == str(uid)
            and _in_window(getattr(r, "started_at", None), start, end)
        ]

        active_sessions = {str(getattr(m, "session_id", "")) for m in day_messages}
        ctx["session_ids"] = session_ids
        return {
            "sessions": len(active_sessions),
            "messages": len(day_messages),
            "runs": len(day_runs),
        }

    async def _gather(
        self, session: Any, user_id: str, day: date, ctx: dict[str, Any]
    ) -> dict[str, Any]:
        """错题聚类、知识点归并（只读；当日新增 ∪ 到期复习的错题为目标集）。"""
        start, end = _day_bounds(day)
        now = end  # 以当日结束时刻为复习到期判定基准（夜间跑批语义）
        uid = _as_uuid(user_id)
        rows = (
            await session.execute(
                select(MistakeRecord).where(MistakeRecord.user_id == uid)
            )
        ).scalars().all()
        records = [r for r in rows if str(getattr(r, "user_id", "")) == str(uid)]
        ctx["all_mistakes"] = records

        targets: dict[str, Any] = {}
        for record in records:
            created = getattr(record, "created_at", None)
            next_review = getattr(record, "next_review_at", None)
            if _in_window(created, start, end):
                targets[str(record.id)] = record
                continue
            if next_review is not None:
                if next_review.tzinfo is None:
                    next_review = next_review.replace(tzinfo=timezone.utc)
                if next_review <= now:
                    targets[str(record.id)] = record

        clusters: dict[str, list[str]] = {}
        for mistake_id, record in targets.items():
            for tag in getattr(record, "knowledge_points", None) or []:
                clusters.setdefault(str(tag), []).append(mistake_id)
        ordered_tags = sorted(clusters, key=lambda t: (-len(clusters[t]), t))  # 确定性
        ctx["targets"] = targets
        ctx["clusters"] = clusters
        ctx["ordered_tags"] = ordered_tags
        return {
            "mistakes_seen": len(targets),
            "clusters": len(clusters),
            "top_tags": ordered_tags[:_MASTERY_MATRIX_TOP],
        }

    async def _consolidate(
        self, session: Any, user_id: str, day: date, ctx: dict[str, Any]
    ) -> dict[str, Any]:
        """掌握度矩阵（派生数据）+ 生成复习卡（标 AI 生成）+ 沉淀长期记忆。

        红线：只 session.add 新派生对象；禁止 UPDATE messages/mistake_records
        的原始内容列（mastery 复习列由 mistake_service 复习路径独占更新）。
        """
        records = ctx.get("all_mistakes") or []
        targets: dict[str, Any] = ctx.get("targets") or {}
        ordered_tags: list[str] = ctx.get("ordered_tags") or []

        matrix = compute_knowledge_mastery(records)
        matrix_top = [
            {"tag": item["knowledge_tag"], "mastery": item["mastery"], "level": item["level"]}
            for item in matrix[:_MASTERY_MATRIX_TOP]
        ]

        tags = ordered_tags[:MAX_CONSOLIDATE_TAGS]
        cards_created = await self._generate_cards(session, user_id, day, tags, targets)

        facts = [
            {"content": f"薄弱知识点：{tag}", "type": "weakness", "importance": 0.7}
            for tag in tags
        ]
        created_memories = await memory_manager.dedup_and_save_memories(
            session, user_id, facts
        )

        return {
            "mistakes_organized": len(targets),
            "cards_created": cards_created,
            "memories_updated": len(created_memories),
            "mastery_matrix": matrix_top,
        }

    async def _generate_cards(
        self,
        session: Any,
        user_id: str,
        day: date,
        tags: list[str],
        targets: dict[str, Any],
    ) -> int:
        """按薄弱知识点生成复习卡（一次小模型调用；失败降级为 0 张不中断）。"""
        if self._llm is None or not tags:
            return 0
        subject = next(
            (getattr(m, "subject", None) for m in targets.values()
             if getattr(m, "subject", None)),
            None,
        )
        try:
            raw = await self._llm.chat_json(
                "你是记忆卡片制作专家，输出严格 JSON 数组。",
                prompts.FLASHCARD_PROMPT.format(
                    content="知识点：" + "、".join(tags), count=min(len(tags) * 2, MAX_DREAM_CARDS)
                ),
            )
        except Exception:  # noqa: BLE001 - 卡片生成失败不拖垮整个整合
            logger.warning("dream 复习卡生成失败，降级为 0 张", exc_info=True)
            return 0
        if not isinstance(raw, list):
            return 0
        deck = f"dream-{day:%Y%m%d}"
        now = datetime.now(timezone.utc)
        created = 0
        for item in raw[:MAX_DREAM_CARDS]:
            try:
                spec = FlashcardSpec.model_validate(item)
            except ValidationError:
                continue  # 单张不合格跳过
            session.add(
                Flashcard(
                    user_id=_as_uuid(user_id),
                    subject=subject,
                    deck=deck,
                    front=f"{AI_GENERATED_MARK}{spec.front}",  # 红线：标"AI 生成"
                    back=spec.back,
                    ease_factor=2.5,
                    repetitions=0,
                    interval_days=0,
                    next_review_at=now,  # 新卡立即到期可学
                )
            )
            created += 1
        if created:
            await session.flush()
        return created

    async def _prune(self, session: Any, user_id: str) -> dict[str, Any]:
        """清理过期数据（默认清 dream_runs 历史；缓存/临时文件经注入扩展）。"""
        result = await self._pruner(session, user_id, now=datetime.now(timezone.utc))
        return {k: int(v) for k, v in (result or {}).items()}


__all__ = [
    "AI_GENERATED_MARK",
    "MAX_DREAM_CARDS",
    "PHASES",
    "DreamPipeline",
    "default_pruner",
]
