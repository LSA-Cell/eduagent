"""学情分析：错题知识点掌握度矩阵 + 班级学情统计聚合。

掌握度矩阵：按 knowledge_points 聚合 mistake_records，
掌握度 = f(正确率代理, 复习次数)。MistakeRecord 无逐次对错流水，
mastery 列在每次复习时按答对/答错更新（见 mistake_service.update_mastery），
因此以 avg(mastery) 作为正确率代理是单调合理的。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import select

from eduagent_shared.models import MistakeRecord

from app.services.mistake_service import MASTERED_REVIEW_COUNT


def compute_knowledge_mastery(records: Sequence[Any]) -> list[dict[str, Any]]:
    """知识点掌握度矩阵（纯函数）。

    输入：MistakeRecord 列表（或含 knowledge_points/mastery/review_count 的对象）。
    输出：按掌握度升序的 [{knowledge_tag, mistake_count, avg_review_count,
    accuracy_proxy, mastery, level}]。
    mastery = 0.5 * 正确率代理 + 0.5 * 复习进度（avg_review_count / 周期数封顶 1）。
    """
    buckets: dict[str, dict[str, float]] = {}
    for record in records:
        tags = getattr(record, "knowledge_points", None) or []
        mastery = float(getattr(record, "mastery", 0.0) or 0.0)
        review_count = int(getattr(record, "review_count", 0) or 0)
        for tag in tags:
            bucket = buckets.setdefault(str(tag), {"count": 0, "mastery_sum": 0.0, "review_sum": 0})
            bucket["count"] += 1
            bucket["mastery_sum"] += mastery
            bucket["review_sum"] += review_count

    matrix: list[dict[str, Any]] = []
    for tag, bucket in buckets.items():
        count = int(bucket["count"])
        avg_mastery = bucket["mastery_sum"] / count
        avg_review = bucket["review_sum"] / count
        review_progress = min(avg_review / MASTERED_REVIEW_COUNT, 1.0)
        mastery = round(0.5 * avg_mastery + 0.5 * review_progress, 4)
        matrix.append({
            "knowledge_tag": tag,
            "mistake_count": count,
            "avg_review_count": round(avg_review, 2),
            "accuracy_proxy": round(avg_mastery, 4),
            "mastery": mastery,
            "level": _mastery_level(mastery),
        })
    matrix.sort(key=lambda item: item["mastery"])  # 最薄弱的排前面
    return matrix


def _mastery_level(mastery: float) -> str:
    if mastery >= 0.8:
        return "mastered"
    if mastery >= 0.5:
        return "familiar"
    if mastery >= 0.2:
        return "weak"
    return "critical"


async def knowledge_mastery(session: Any, user_id: str) -> list[dict[str, Any]]:
    """读取用户全部错题并计算掌握度矩阵。"""
    stmt = select(MistakeRecord).where(MistakeRecord.user_id == user_id)
    rows = await session.execute(stmt)
    return compute_knowledge_mastery(list(rows.scalars().all()))


async def aggregate_mistake_stats(
    session: Any,
    user_ids: Sequence[str],
    days: Optional[int] = None,
) -> dict[str, Any]:
    """聚合一组学生的错题统计，供学情分析 LLM 生成报告。

    data_source 约定：{"student_ids": [...], "days": 30}（可选）。
    """
    stmt = select(MistakeRecord).where(MistakeRecord.user_id.in_([str(u) for u in user_ids]))
    if days:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = stmt.where(MistakeRecord.created_at >= since)
    rows = await session.execute(stmt)
    records = list(rows.scalars().all())

    by_tag: dict[str, int] = {}
    by_subject: dict[str, int] = {}
    trend: dict[str, int] = {}
    for record in records:
        subject = getattr(record, "subject", None) or "未分科"
        by_subject[subject] = by_subject.get(subject, 0) + 1
        created = getattr(record, "created_at", None)
        if created is not None:
            day_key = created.date().isoformat()
            trend[day_key] = trend.get(day_key, 0) + 1
        for tag in getattr(record, "knowledge_points", None) or []:
            by_tag[str(tag)] = by_tag.get(str(tag), 0) + 1

    return {
        "student_count": len(user_ids),
        "total_mistakes": len(records),
        "by_knowledge_tag": dict(sorted(by_tag.items(), key=lambda kv: kv[1], reverse=True)),
        "by_subject": by_subject,
        "trend": [{"date": d, "count": c} for d, c in sorted(trend.items())],
    }
