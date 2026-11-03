"""学情任务必须使用授权的数据源，成绩文件解析有明确边界。"""

from __future__ import annotations

import io
import uuid
import xml.etree.ElementTree as ET
import zipfile
from types import SimpleNamespace

import pytest

from eduagent_shared.models import LearningAnalysis

from app.services import analytics_service, content_gen
from app.tasks import gen_tasks
from tests.conftest import FakeSession, as_factory


def _xlsx(content: str) -> bytes:
    sheet = ("<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
             "<sheetData><row r='1'><c r='A1' t='inlineStr'><is><t>姓名</t></is></c>"
             "<c r='B1' t='inlineStr'><is><t>分数</t></is></c></row>"
             f"<row r='2'><c r='A2' t='inlineStr'><is><t>学生</t></is></c>"
             f"<c r='B2'><v>{content}</v></c></row></sheetData></worksheet>")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


@pytest.mark.parametrize("extension,data,average", [
    ("csv", "姓名,分数\n甲,80\n乙,90\n".encode(), 85),
    ("xlsx", _xlsx("75"), 75),
])
def test_score_file_uses_uploaded_values(extension, data, average):
    stats = analytics_service.aggregate_score_file(data, extension)
    assert stats["source"] == extension
    assert stats["average_score"] == average
    assert stats["student_count"] == (2 if extension == "csv" else 1)


@pytest.mark.parametrize("extension,data", [
    ("xls", b"old Excel"),
    ("csv", b"name,score\nA,not-a-number\n"),
    ("csv", b"name,score\nA,1001\n"),
    ("xlsx", b"not a workbook"),
    ("xlsx", _xlsx("<broken>")),
])
def test_unsupported_or_invalid_score_file_fails_closed(extension, data):
    with pytest.raises((ValueError, zipfile.BadZipFile, ET.ParseError)):
        analytics_service.aggregate_score_file(data, extension)


def test_report_url_must_match_public_origin_bucket_and_owner():
    user = str(uuid.uuid4())
    key = f"u/{user}/{'a' * 32}.csv"
    url = f"https://files.example/eduagent-uploads/{key}?X-Amz-Signature=signed"
    assert analytics_service.report_object_key(
        url, user, "files.example", True, "eduagent-uploads",
    ) == (key, "csv")
    for bad in (
        url.replace("files.example", "evil.example"),
        url.replace(user, str(uuid.uuid4())),
        url.replace("eduagent-uploads", "other-bucket"),
        url.replace("?X-Amz-Signature=signed", ""),
    ):
        with pytest.raises(ValueError):
            analytics_service.report_object_key(
                bad, user, "files.example", True, "eduagent-uploads",
            )


@pytest.mark.asyncio
async def test_mistake_query_filters_user_and_selected_ids():
    statements = []

    def capture(stmt, _params):
        statements.append(stmt)
        return []

    selected = uuid.uuid4()
    await analytics_service.aggregate_mistake_stats(
        FakeSession(sql_handler=capture), [str(uuid.uuid4())], mistake_ids=[selected],
    )
    sql = str(statements[0])
    assert "mistake_records.user_id IN" in sql
    assert "mistake_records.id IN" in sql


async def _record(session: FakeSession, data_source: dict, analysis_type: str = "mistake"):
    record = LearningAnalysis(
        user_id=uuid.uuid4(), analysis_type=analysis_type,
        metrics={"data_source": data_source, "status": "generating"}, suggestions=[],
    )
    session.add(record)
    await session.flush()
    return record


@pytest.mark.asyncio
async def test_selected_mistakes_are_scoped_to_owner(monkeypatch):
    session = FakeSession()
    selected = uuid.uuid4()
    record = await _record(session, {"mistake_ids": [str(selected), str(selected)]})
    seen = {}

    async def aggregate(_session, users, days=None, mistake_ids=None):
        seen.update(users=users, ids=mistake_ids)
        return {"total_mistakes": 1}

    async def generate(_session, _llm, _docgen, _record, stats):
        seen["stats"] = stats
        return {"status": "completed"}

    monkeypatch.setattr(analytics_service, "aggregate_mistake_stats", aggregate)
    monkeypatch.setattr(content_gen, "run_analysis_generation", generate)
    result = await gen_tasks.run_generate_analysis(
        str(record.id), session_factory=as_factory(session), llm=object(), docgen=object(),
    )
    assert result["status"] == "completed"
    assert seen == {"users": [str(record.user_id)], "ids": [selected],
                    "stats": {"total_mistakes": 1}}


@pytest.mark.asyncio
async def test_foreign_student_ids_fail_without_query(monkeypatch):
    session = FakeSession()
    record = await _record(session, {"student_ids": [str(uuid.uuid4())]})

    async def forbidden(*args, **kwargs):
        raise AssertionError("must not query another student's mistakes")

    monkeypatch.setattr(analytics_service, "aggregate_mistake_stats", forbidden)
    result = await gen_tasks.run_generate_analysis(
        str(record.id), session_factory=as_factory(session), llm=object(), docgen=object(),
    )
    assert result["status"] == "failed"
    assert record.metrics["status"] == "failed"


@pytest.mark.asyncio
async def test_score_file_uses_internal_object_key_and_uploaded_bytes(monkeypatch):
    session = FakeSession()
    record = await _record(session, {}, "score")
    key = f"u/{record.user_id}/{'a' * 32}.csv"
    record.metrics["data_source"] = {
        "file_url": f"https://files.example/eduagent-uploads/{key}?X-Amz-Signature=signed"
    }
    settings = SimpleNamespace(minio_public_endpoint="files.example", minio_public_use_ssl=True,
                               minio_bucket_uploads="eduagent-uploads")
    seen = {}
    monkeypatch.setattr(gen_tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(gen_tasks.storage_service, "stat_object",
                        lambda object_key, **kwargs: SimpleNamespace(size=22))
    monkeypatch.setattr(gen_tasks.storage_service, "download_bytes",
                        lambda object_key, **kwargs: b"name,score\nA,80\nB,90\n")

    async def generate(_session, _llm, _docgen, _record, stats):
        seen.update(stats)
        return {"status": "completed"}

    monkeypatch.setattr(content_gen, "run_analysis_generation", generate)
    result = await gen_tasks.run_generate_analysis(
        str(record.id), session_factory=as_factory(session), llm=object(), docgen=object(),
    )
    assert result["status"] == "completed"
    assert seen["average_score"] == 85
    assert seen["source"] == "csv"


@pytest.mark.asyncio
async def test_malformed_xlsx_marks_task_failed(monkeypatch):
    session = FakeSession()
    record = await _record(session, {}, "score")
    key = f"u/{record.user_id}/{'a' * 32}.xlsx"
    record.metrics["data_source"] = {
        "file_url": f"https://files.example/eduagent-uploads/{key}?X-Amz-Signature=signed"
    }
    settings = SimpleNamespace(minio_public_endpoint="files.example", minio_public_use_ssl=True,
                               minio_bucket_uploads="eduagent-uploads")
    monkeypatch.setattr(gen_tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(gen_tasks.storage_service, "stat_object",
                        lambda object_key, **kwargs: SimpleNamespace(size=500))
    monkeypatch.setattr(gen_tasks.storage_service, "download_bytes",
                        lambda object_key, **kwargs: _xlsx("<broken>"))

    async def forbidden(*args, **kwargs):
        raise AssertionError("malformed spreadsheet must not reach LLM")

    monkeypatch.setattr(content_gen, "run_analysis_generation", forbidden)
    result = await gen_tasks.run_generate_analysis(
        str(record.id), session_factory=as_factory(session), llm=object(), docgen=object(),
    )
    assert result["status"] == "failed"
    assert record.metrics["status"] == "failed"
    assert "<broken>" not in str(record.metrics)
