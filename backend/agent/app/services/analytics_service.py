"""学情分析：错题知识点掌握度矩阵 + 班级学情统计聚合。

掌握度矩阵：按 knowledge_points 聚合 mistake_records，
掌握度 = f(正确率代理, 复习次数)。MistakeRecord 无逐次对错流水，
mastery 列在每次复习时按答对/答错更新（见 mistake_service.update_mastery），
因此以 avg(mastery) 作为正确率代理是单调合理的。
"""

from __future__ import annotations

import csv
import io
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence
from urllib.parse import urlsplit

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
    mistake_ids: Optional[Sequence[uuid.UUID]] = None,
) -> dict[str, Any]:
    """聚合一组学生的错题统计，供学情分析 LLM 生成报告。

    data_source 约定：{"student_ids": [...], "days": 30}（可选）。
    """
    stmt = select(MistakeRecord).where(MistakeRecord.user_id.in_([str(u) for u in user_ids]))
    if mistake_ids is not None:
        stmt = stmt.where(MistakeRecord.id.in_(list(mistake_ids)))
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


MAX_REPORT_BYTES = 5 * 1024 * 1024
MAX_REPORT_ROWS = 10_000
MAX_REPORT_COLUMNS = 100
_SCORE_HEADERS = {"score", "scores", "mark", "marks", "grade", "成绩", "分数", "得分",
                  "总分", "语文", "数学", "英语", "物理", "化学", "生物", "历史", "政治", "地理"}


def report_object_key(file_url: str, user_id: str, public_endpoint: str,
                      public_ssl: bool, bucket: str) -> tuple[str, str]:
    """只从本用户预期的签名地址提取对象键；绝不请求 URL 指定的主机。"""
    parsed = urlsplit(file_url)
    expected_scheme = "https" if public_ssl else "http"
    if parsed.scheme != expected_scheme or parsed.netloc != public_endpoint:
        raise ValueError("invalid report file origin")
    # 签名仅用于确认输入形态；任务排队后它可能过期。真正的读取使用内部
    # MinIO 客户端，并且对象键必须严格属于当前用户。
    if "X-Amz-Signature=" not in parsed.query:
        raise ValueError("report file URL is unsigned")
    match = re.fullmatch(
        rf"/{re.escape(bucket)}/(u/{re.escape(user_id)}/[0-9a-f]{{32}}\.(csv|xlsx))",
        parsed.path,
    )
    if match is None:
        raise ValueError("invalid report file key")
    return match.group(1), match.group(2)


def _score_stats(rows: Sequence[Sequence[str]], source: str) -> dict[str, Any]:
    if not rows:
        raise ValueError("empty score file")
    headers = [str(value).strip().lower() for value in rows[0]]
    score_columns = [i for i, name in enumerate(headers)
                     if name in _SCORE_HEADERS or name.endswith(("成绩", "分数", "得分"))]
    if not score_columns:
        raise ValueError("score column missing")
    scores: list[float] = []
    student_rows = 0
    for row in rows[1:]:
        if not any(str(value).strip() for value in row):
            continue
        student_rows += 1
        for index in score_columns:
            if index >= len(row) or not str(row[index]).strip():
                continue
            try:
                score = float(str(row[index]).strip())
            except ValueError as exc:
                raise ValueError("non-numeric score") from exc
            if not 0 <= score <= 1000:
                raise ValueError("score outside supported range")
            scores.append(score)
    if not scores:
        raise ValueError("score file has no scores")
    return {
        "source": source,
        "student_count": student_rows,
        "score_count": len(scores),
        "average_score": round(sum(scores) / len(scores), 2),
        "minimum_score": min(scores),
        "maximum_score": max(scores),
    }


def aggregate_score_file(data: bytes, extension: str) -> dict[str, Any]:
    """对有限大小的 CSV/XLSX 成绩表统计数值，拒绝未知或超大输入。"""
    if not data or len(data) > MAX_REPORT_BYTES:
        raise ValueError("report file size outside supported range")
    if extension == "csv":
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    elif extension == "xlsx":
        with zipfile.ZipFile(io.BytesIO(data)) as workbook:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in workbook.namelist():
                info = workbook.getinfo("xl/sharedStrings.xml")
                if info.file_size > MAX_REPORT_BYTES:
                    raise ValueError("xlsx strings too large")
                root = ET.fromstring(workbook.read(info))
                shared = ["".join(node.itertext()) for node in root]
            sheets = sorted(name for name in workbook.namelist()
                            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name))
            if not sheets:
                raise ValueError("xlsx has no worksheet")
            info = workbook.getinfo(sheets[0])
            if info.file_size > MAX_REPORT_BYTES:
                raise ValueError("xlsx worksheet too large")
            root = ET.fromstring(workbook.read(info))
            namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            rows = []
            for row in root.iter(namespace + "row"):
                cells: list[str] = []
                for cell in row.findall(namespace + "c"):
                    address = cell.get("r", "")
                    letters = re.match(r"[A-Z]+", address)
                    if letters is None:
                        continue
                    index = 0
                    for letter in letters.group():
                        index = index * 26 + ord(letter) - ord("A") + 1
                    index -= 1
                    if index >= MAX_REPORT_COLUMNS:
                        raise ValueError("xlsx has too many columns")
                    while len(cells) <= index:
                        cells.append("")
                    value = cell.find(namespace + "v")
                    if cell.get("t") == "inlineStr":
                        inline = cell.find(namespace + "is")
                        cells[index] = "".join(inline.itertext()) if inline is not None else ""
                    elif value is not None and cell.get("t") == "s":
                        shared_index = int(value.text or "-1")
                        if not 0 <= shared_index < len(shared):
                            raise ValueError("invalid xlsx shared string index")
                        cells[index] = shared[shared_index]
                    elif value is not None:
                        cells[index] = value.text or ""
                rows.append(cells)
                if len(rows) > MAX_REPORT_ROWS:
                    raise ValueError("xlsx has too many rows")
    else:
        raise ValueError("unsupported report file format")
    if len(rows) > MAX_REPORT_ROWS or any(len(row) > MAX_REPORT_COLUMNS for row in rows):
        raise ValueError("report file exceeds row or column limit")
    return _score_stats(rows, extension)
