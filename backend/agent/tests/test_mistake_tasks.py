"""错题提取任务测试（mock LLM 返回 + 假 DB 会话，离线）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from eduagent_shared.models import Message, MistakeRecord

from app.services.llm_client import LLMError
from app.tasks import mistake_tasks
from tests.conftest import FakeLLM, FakeSession, as_factory

SESSION_ID = "s-test"
USER_ID = "u-test"

MISTAKE_JSON = {
    "is_mistake": True,
    "question": "解方程 x^2 - 4 = 0",
    "user_answer": "x = 2",
    "correct_answer": "x = ±2",
    "analysis": "开平方漏掉负根",
    "knowledge_tags": ["一元二次方程", "平方根"],
}


def _seed_dialogue(session: FakeSession) -> None:
    session.add(Message(session_id=SESSION_ID, role="user", content="这题怎么做？x^2-4=0"))
    session.add(Message(session_id=SESSION_ID, role="assistant",
                        content="<step>第1步：移项</step> 学生作答：x=2"))


async def test_extract_mistake_creates_record(fake_session: FakeSession) -> None:
    _seed_dialogue(fake_session)
    await fake_session.flush()
    llm = FakeLLM(json_result=dict(MISTAKE_JSON))

    result = await mistake_tasks.run_extract_mistake(
        USER_ID, SESSION_ID, subject="数学", source_message_id="m-1",
        session_factory=as_factory(fake_session), llm=llm,
    )

    assert result == {"created": True, "mistake_id": result["mistake_id"]}
    records = [o for o in fake_session.added if isinstance(o, MistakeRecord)]
    assert len(records) == 1
    record = records[0]
    assert record.wrong_answer == "x = 2"  # LLM 字段 user_answer → wrong_answer
    assert record.correct_answer == "x = ±2"
    assert record.knowledge_points == ["一元二次方程", "平方根"]
    assert record.subject == "数学"
    assert record.review_count == 0 and record.mastery == 0.0
    # 艾宾浩斯首次复习：录入 +1 天
    assert record.next_review_at is not None
    assert record.next_review_at <= datetime.now(timezone.utc) + timedelta(days=1, minutes=5)
    assert record.next_review_at > datetime.now(timezone.utc)
    assert fake_session.commits == 1
    # prompt 中带上了最近对话
    assert "x^2-4=0" in llm.json_calls[0]["user"]


async def test_extract_mistake_not_a_mistake(fake_session: FakeSession) -> None:
    _seed_dialogue(fake_session)
    await fake_session.flush()
    llm = FakeLLM(json_result={"is_mistake": False})

    result = await mistake_tasks.run_extract_mistake(
        USER_ID, SESSION_ID, session_factory=as_factory(fake_session), llm=llm
    )

    assert result == {"created": False}
    assert not [o for o in fake_session.added if isinstance(o, MistakeRecord)]


async def test_extract_mistake_llm_error_graceful(fake_session: FakeSession) -> None:
    _seed_dialogue(fake_session)
    await fake_session.flush()
    llm = FakeLLM(json_result=LLMError("网关超时"))

    result = await mistake_tasks.run_extract_mistake(
        USER_ID, SESSION_ID, session_factory=as_factory(fake_session), llm=llm
    )

    assert result == {"created": False, "reason": "llm_error"}
    assert fake_session.rollbacks == 1


async def test_extract_mistake_empty_dialogue(fake_session: FakeSession) -> None:
    result = await mistake_tasks.run_extract_mistake(
        USER_ID, SESSION_ID, session_factory=as_factory(fake_session), llm=FakeLLM()
    )
    assert result == {"created": False, "reason": "empty_dialogue"}


def test_extract_mistake_celery_registered() -> None:
    """任务在 Celery 应用中注册（eager 模式 .delay 可直接调用）。"""
    from app.tasks import gen_tasks, memory_tasks  # noqa: F401 - 导入即注册
    from app.tasks.celery_app import celery_app

    assert "agent.mistake.extract" in celery_app.tasks
    assert "agent.memory.extract" in celery_app.tasks
    assert "agent.gen.presentation" in celery_app.tasks
    assert "agent.gen.plan_schedule" in celery_app.tasks
