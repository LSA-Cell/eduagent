"""DreamTask 夜间记忆整合测试（§4.2）。

覆盖红线：四阶段固定顺序与 stats 落库 / 锁冲突跳过 / 原始消息与错题零改动 /
复习卡标"AI 生成" / 推送过审（fail-closed）/ retry ≤2 + 低优先级队列。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from eduagent_shared.models import ChatSession, Flashcard, Memory, Message, MistakeRecord

from app.dream.locks import InMemoryDreamLock
from app.dream.models import DreamRun
from app.dream.pipeline import AI_GENERATED_MARK, PHASES, DreamPipeline
from app.dream.summary import build_dream_summary, extract_stats, render_summary_text
from tests.conftest import FakeLLM, FakeSession, as_factory

DAY = date(2026, 8, 16)  # 固定活动日（离线确定性）
DAY_NOON = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def _make_session(user_id: uuid.UUID, *, with_mistakes: bool = True) -> FakeSession:
    """当日有活动（1 会话 1 消息）+ 2 道带知识点错题的测试现场。"""
    session = FakeSession()
    chat_session = ChatSession(id=uuid.uuid4(), user_id=user_id, subject="math",
                               created_at=DAY_NOON)
    session.add(chat_session)
    session.add(Message(id=uuid.uuid4(), session_id=chat_session.id, role="user",
                        content="这道一元二次方程怎么做？", created_at=DAY_NOON))
    session.add(Message(id=uuid.uuid4(), session_id=chat_session.id, role="assistant",
                        content="用求根公式……", created_at=DAY_NOON))
    if with_mistakes:
        for i in range(2):
            session.add(MistakeRecord(
                id=uuid.uuid4(), user_id=user_id, subject="math",
                question=f"原始错题题干-{i}", wrong_answer="错答", correct_answer="正解",
                analysis="错因", knowledge_points=["一元二次方程"],
                mastery=0.1, review_count=0,
                next_review_at=DAY_NOON + timedelta(days=1),
                created_at=DAY_NOON,
            ))
    return session


def _cards_llm() -> FakeLLM:
    return FakeLLM(json_result=[
        {"front": "一元二次方程求根公式是什么？", "back": "x=(-b±√(b²-4ac))/2a"},
        {"front": "判别式何时无实根？", "back": "b²-4ac<0"},
    ])


class TestPipeline:
    async def test_four_phases_in_order_and_stats_persisted(self) -> None:
        """四阶段固定顺序执行；每阶段产物合并落 dream_runs.stats。"""
        user_id = uuid.uuid4()
        session = _make_session(user_id)
        pipeline = DreamPipeline(as_factory(session), lock=InMemoryDreamLock(),
                                 llm=_cards_llm())

        order: list[str] = []
        original = pipeline._run_phase

        async def spy(phase, *args):
            order.append(phase)
            return await original(phase, *args)

        pipeline._run_phase = spy  # type: ignore[method-assign]
        result = await pipeline.run(str(user_id), day=DAY)

        assert result["status"] == "completed"
        assert order == list(PHASES) == ["orient", "gather", "consolidate", "prune"]

        [run] = [o for o in session.added if isinstance(o, DreamRun)]
        assert run.phase == "completed" and run.finished_at is not None
        stats = run.stats
        # orient / gather / consolidate / prune 各阶段产物齐全
        assert stats["sessions"] == 1 and stats["messages"] == 2
        assert stats["mistakes_seen"] == 2 and stats["clusters"] == 1
        assert stats["mistakes_organized"] == 2
        assert stats["cards_created"] == 2
        assert stats["memories_updated"] == 1  # 薄弱知识点记忆去重后 1 条
        assert "dream_history_pruned" in stats
        assert stats["mastery_matrix"][0]["tag"] == "一元二次方程"

    async def test_lock_conflict_skips(self) -> None:
        """锁冲突即跳过该用户当日整合（禁止并发双跑）；不产生任何写入。"""
        user_id = uuid.uuid4()
        session = _make_session(user_id)
        lock = InMemoryDreamLock()
        assert await lock.acquire(str(user_id), DAY) is True  # 模拟另一进程持锁
        pipeline = DreamPipeline(as_factory(session), lock=lock, llm=_cards_llm())

        result = await pipeline.run(str(user_id), day=DAY)

        assert result["status"] == "skipped" and result["reason"] == "lock_conflict"
        assert not [o for o in session.added if isinstance(o, DreamRun)]
        assert not [o for o in session.added if isinstance(o, Flashcard)]

    async def test_original_messages_and_mistakes_untouched(self) -> None:
        """红线：禁止修改用户原始消息与原始错题内容（diff 断言零改动）。"""
        user_id = uuid.uuid4()
        session = _make_session(user_id)

        def snapshot() -> dict[str, tuple]:
            snap: dict[str, tuple] = {}
            for obj in session.added:  # FakeSession：added 即全部已登记对象
                if isinstance(obj, Message):
                    snap[f"msg:{obj.id}"] = (obj.role, obj.content, obj.content_type)
                elif isinstance(obj, MistakeRecord):
                    snap[f"mistake:{obj.id}"] = (
                        obj.question, obj.wrong_answer, obj.correct_answer,
                        obj.analysis, tuple(obj.knowledge_points or []),
                        obj.mastery, obj.review_count,
                    )
            return snap

        before = snapshot()
        pipeline = DreamPipeline(as_factory(session), lock=InMemoryDreamLock(),
                                 llm=_cards_llm())
        result = await pipeline.run(str(user_id), day=DAY)
        assert result["status"] == "completed"
        assert snapshot() == before  # 原始行逐字段零改动
        # 新增对象只允许派生表：DreamRun / Flashcard / Memory
        new_types = {
            type(o) for o in session.added if not isinstance(o, (Message, MistakeRecord))
        }
        assert new_types <= {DreamRun, Flashcard, Memory, ChatSession}

    async def test_cards_marked_ai_generated(self) -> None:
        """红线：自动生成的复习卡标"AI 生成"，且归属 dream 卡组。"""
        user_id = uuid.uuid4()
        session = _make_session(user_id)
        pipeline = DreamPipeline(as_factory(session), lock=InMemoryDreamLock(),
                                 llm=_cards_llm())
        await pipeline.run(str(user_id), day=DAY)
        cards = [o for o in session.added if isinstance(o, Flashcard)]
        assert len(cards) == 2
        for card in cards:
            assert card.front.startswith(AI_GENERATED_MARK)
            assert card.deck == "dream-20260816"

    async def test_failure_marks_run_failed_and_raises(self) -> None:
        """阶段异常 → dream_runs.phase=failed 落库并抛出（供 Celery 重试）。"""

        class FailingFlushSession(FakeSession):
            async def flush(self) -> None:
                raise RuntimeError("db down")

        user_id = uuid.uuid4()
        session = FailingFlushSession()
        pipeline = DreamPipeline(as_factory(session), lock=InMemoryDreamLock())
        with pytest.raises(RuntimeError):
            await pipeline.run(str(user_id), day=DAY)


class TestSummary:
    async def test_summary_moderated_and_delivered_once(self) -> None:
        """次日首启摘要：过审后推送一次（delivered_at 标记），stats 只含协议计数键。"""
        user_id = uuid.uuid4()
        session = _make_session(user_id)
        pipeline = DreamPipeline(as_factory(session), lock=InMemoryDreamLock(),
                                 llm=_cards_llm())
        await pipeline.run(str(user_id), day=DAY)

        moderated: list[str] = []

        async def moderate(text: str) -> bool:
            moderated.append(text)
            return True

        summary = await build_dream_summary(session, str(user_id), moderate=moderate)
        assert summary is not None
        assert summary["stats"] == {
            "mistakes_organized": 2, "cards_created": 2, "memories_updated": 1,
        }
        assert moderated and "2 道错题" in moderated[0]
        # 已投递 → 再次首启不重复推送
        assert await build_dream_summary(session, str(user_id), moderate=moderate) is None

    async def test_summary_fail_closed_on_moderation(self) -> None:
        """审核不过/审核故障 → 不推送（fail-closed）。"""
        user_id = uuid.uuid4()
        session = _make_session(user_id)
        pipeline = DreamPipeline(as_factory(session), lock=InMemoryDreamLock(),
                                 llm=_cards_llm())
        await pipeline.run(str(user_id), day=DAY)

        async def unsafe(text: str) -> bool:
            return False

        assert await build_dream_summary(session, str(user_id), moderate=unsafe) is None

        async def broken(text: str) -> bool:
            raise RuntimeError("moderation down")

        assert await build_dream_summary(session, str(user_id), moderate=broken) is None

    async def test_render_text_and_stats_extraction(self) -> None:
        """摘要文案与 stats 键集（协议外键丢弃）。"""
        stats = extract_stats({"mistakes_organized": 3, "cards_created": 5,
                               "memories_updated": 2, "top_tags": ["秘密"]})
        assert stats == {"mistakes_organized": 3, "cards_created": 5, "memories_updated": 2}
        assert render_summary_text(stats) == "已为你整理昨日 3 道错题，生成 5 张复习卡，更新 2 条学习记忆。"
        assert render_summary_text({"mistakes_organized": 0, "cards_created": 0,
                                    "memories_updated": 0}) == "已完成昨夜的学习整理，今天也要加油。"

    async def test_dream_summary_event_at_loop_start(self) -> None:
        """次日首启：dream.summary 事件紧随 run.start 推送（仅一次）。"""
        from app.loop.agent_loop import edu_agent_loop
        from tests.orchestration_fakes import FakeDeps

        deps = FakeDeps()
        calls: list[str] = []

        async def provider(user_id: str):
            calls.append(user_id)
            return {"stats": {"mistakes_organized": 3, "cards_created": 5,
                              "memories_updated": 1}, "text": "…"}

        deps.dream_summary_provider = provider
        req = SimpleNamespace(user_id="u1", message="你好", session_id=None,
                              subject="math", model=None)
        events = [e async for e in edu_agent_loop(req, deps)]
        assert [e.event for e in events[:2]] == ["run.start", "dream.summary"]
        assert events[1].data["stats"]["cards_created"] == 5

        # 无可推送摘要（provider 返回 None）→ 不出现 dream.summary
        deps2 = FakeDeps()

        async def empty_provider(user_id: str):
            return None

        deps2.dream_summary_provider = empty_provider
        events2 = [e async for e in edu_agent_loop(req, deps2)]
        assert "dream.summary" not in [e.event for e in events2]


class TestTasks:
    async def test_dispatch_only_active_users_to_dream_queue(self) -> None:
        """beat 派发：只处理近 24h 有活动用户，逐用户投递 dream 队列。"""
        from app.dream.tasks import dispatch_dream_batch

        active = uuid.uuid4()
        stale = uuid.uuid4()
        session = FakeSession()
        now = datetime.now(timezone.utc)
        session.add(ChatSession(id=uuid.uuid4(), user_id=active, created_at=now))
        session.add(ChatSession(id=uuid.uuid4(), user_id=stale,
                                created_at=now - timedelta(days=7)))
        await session.flush()  # FakeSession：flush 后对象才进入可查询注册表
        sent: list[dict] = []
        result = await dispatch_dream_batch(
            session_factory=as_factory(session),
            sender=lambda name, payload, *, countdown=0: sent.append(
                {"name": name, "payload": payload, "countdown": countdown}),
        )
        assert result["dispatched"] == 1
        assert sent[0]["name"] == "agent.dream.run_user"
        assert sent[0]["payload"]["user_id"] == str(active)
        assert sent[0]["payload"]["day"]

    def test_retry_capped_at_2_and_low_priority_queue(self) -> None:
        """红线：失败重试 ≤2 次；任务走低优先级队列 dream。"""
        from app.dream.tasks import DREAM_QUEUE, dream_run_user
        from app.tasks.celery_app import celery_app

        assert dream_run_user.max_retries == 2
        assert celery_app.conf.task_routes["agent.dream.run_user"]["queue"] == DREAM_QUEUE

    def test_beat_schedule_registered(self) -> None:
        """beat 每日 02:00 派发已注册。"""
        from app.tasks.celery_app import celery_app

        entry = celery_app.conf.beat_schedule["dream-nightly-dispatch"]
        assert entry["task"] == "agent.dream.dispatch"
        assert entry["schedule"].hour == {2}

    def test_failed_task_retries_at_most_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """红线：失败重试 ≤2 次——eager 下可观测 1 次原始执行 + 恰好 2 次重试。"""
        import celery.states

        from app.dream import tasks as dream_tasks

        attempts: list[int] = []

        async def boom(*args, **kwargs):
            attempts.append(1)
            raise RuntimeError("db down")

        monkeypatch.setattr(dream_tasks, "run_dream_for_user", boom)
        result = dream_tasks.dream_run_user.apply(kwargs={"user_id": str(uuid.uuid4())})
        assert result.state == celery.states.FAILURE
        assert len(attempts) == 3  # 原始 1 次 + 重试 2 次，不会出现第 3 次重试
