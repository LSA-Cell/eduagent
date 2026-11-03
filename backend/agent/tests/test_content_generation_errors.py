"""教师生成失败不得把内部上游诊断写入产物或返回给客户端。"""

from types import SimpleNamespace

import pytest

from app.services import content_gen
from tests.conftest import FakeLLM, FakeSession, TEST_USER_ID


INTERNAL_ERROR = (
    "upstream http://gateway:8001/v1/chat/completions "
    "/app/proxy/private.py key=private-test-value"
)
FAILURE_MESSAGE = "内容生成失败，请稍后重试"
CASES = {
    "ppt": (
        content_gen.run_ppt_generation, content_gen.presentation_to_dict,
        "outline", "ready",
        {"slides": [{"slide_number": 1, "title": "复习", "content_points": ["函数"]}]},
    ),
    "lesson": (
        content_gen.run_lesson_plan_generation, content_gen.lesson_plan_to_dict,
        "content", "completed",
        {"objectives": ["理解函数"], "procedure": [{"stage": "讲授", "duration_minutes": 20}]},
    ),
    "exam": (
        content_gen.run_exam_paper_generation, content_gen.exam_paper_to_dict,
        "answer_key", "completed",
        {"questions": [{"number": 1, "type": "short_answer", "question": "2+2=?", "answer": "4", "score": 5}]},
    ),
    "analysis": (
        content_gen.run_analysis_generation, content_gen.analysis_to_dict,
        "metrics", "completed", {"summary": "继续练习", "suggestions": ["复习函数"]},
    ),
}


class FakeDocgen:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def render(self, payload):
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        return {"file_url": "https://storage.example/result?X-Amz-Signature=test"}

    render_ppt = render
    render_lesson_plan = render
    render_exam_paper = render
    render_analysis = render


async def make_record(kind, session):
    if kind == "ppt":
        return await content_gen.create_presentation(session, TEST_USER_ID, SimpleNamespace(
            topic="函数", subject="数学", grade="初一", style="default", pages=1,
        ))
    if kind == "lesson":
        return await content_gen.create_lesson_plan(session, TEST_USER_ID, SimpleNamespace(
            course_name="函数", subject="数学", grade="初一", duration=45,
            objectives=["理解函数"], template_type="standard",
        ))
    if kind == "exam":
        return await content_gen.create_exam_paper(session, TEST_USER_ID, SimpleNamespace(
            grade="初一", subject="数学", difficulty="medium", question_types=["short_answer"], count=1,
        ))
    return await content_gen.create_analysis(session, TEST_USER_ID, SimpleNamespace(
        class_name="一班", analysis_type="weekly", data_source={},
    ))


async def run_generation(kind, runner, session, llm, docgen, record):
    if kind == "analysis":
        return await runner(session, llm, docgen, record, stats={})
    return await runner(session, llm, docgen, record)


@pytest.mark.parametrize("kind", CASES)
@pytest.mark.parametrize("stage", ["llm", "docgen"])
async def test_generation_failures_redact_storage_response_and_logs(kind, stage, monkeypatch):
    runner, to_dict, store_name, _, valid_json = CASES[kind]
    session = FakeSession()
    record = await make_record(kind, session)
    logged = []
    monkeypatch.setattr(content_gen, "log", SimpleNamespace(
        warning=lambda *args, **kwargs: logged.append((args, kwargs)),
    ), raising=False)
    llm = FakeLLM(json_result=RuntimeError(INTERNAL_ERROR) if stage == "llm" else valid_json)
    docgen = FakeDocgen(RuntimeError(INTERNAL_ERROR) if stage == "docgen" else None)

    result = await run_generation(kind, runner, session, llm, docgen, record)

    assert result["status"] == "failed"
    assert getattr(record, store_name)["_error"] == FAILURE_MESSAGE
    response = to_dict(record)
    assert response["status"] == "failed"
    assert response["error"] == FAILURE_MESSAGE
    assert len(logged) == 1
    assert logged[0][1] == {"error_type": "RuntimeError"}
    for output in [getattr(record, store_name), response, logged]:
        assert "private-test-value" not in str(output)
        assert "gateway:8001" not in str(output)
        assert "/app/proxy/private.py" not in str(output)
    assert len(docgen.calls) == (0 if stage == "llm" else 1)


@pytest.mark.parametrize("kind", CASES)
async def test_historical_raw_errors_are_not_returned(kind):
    _, to_dict, store_name, _, _ = CASES[kind]
    record = await make_record(kind, FakeSession())
    getattr(record, store_name)["_error"] = INTERNAL_ERROR
    response = to_dict(record)
    assert response["error"] == FAILURE_MESSAGE
    assert INTERNAL_ERROR not in str(response)
    assert "private-test-value" not in str(response)


@pytest.mark.parametrize("kind", CASES)
async def test_successful_generation_keeps_existing_result_contract(kind):
    runner, to_dict, store_name, success_status, valid_json = CASES[kind]
    session = FakeSession()
    record = await make_record(kind, session)
    getattr(record, store_name)["_error"] = INTERNAL_ERROR
    docgen = FakeDocgen()

    result = await run_generation(kind, runner, session, FakeLLM(json_result=valid_json), docgen, record)

    assert result["status"] == success_status
    assert to_dict(record)["status"] == success_status
    assert to_dict(record)["error"] is None
    assert "_error" not in getattr(record, store_name)
    assert len(docgen.calls) == 1
