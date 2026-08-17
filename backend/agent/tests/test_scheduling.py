"""复习调度测试：错题艾宾浩斯推进 + 记忆卡片 SM-2。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from eduagent_shared.models import Flashcard, MistakeRecord

from app.services import flashcard_service, mistake_service
from app.services.flashcard_service import MIN_EASE_FACTOR, sm2_update
from app.services.mistake_service import compute_next_review_at, update_mastery
from tests.conftest import FakeSession

BASE = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
USER = "u-1"


# ---------------------------------------------------------------------------
# 艾宾浩斯（错题本）
# ---------------------------------------------------------------------------
class TestEbbinghaus:
    @pytest.mark.parametrize(
        "review_count,expected_days",
        [(0, 1), (1, 2), (2, 4), (3, 7), (4, 15), (5, 30), (6, 30), (99, 30)],
    )
    def test_interval_progression(self, review_count: int, expected_days: int) -> None:
        """1/2/4/7/15/30 天序列，超出周期后停在 30 天。"""
        assert compute_next_review_at(review_count, base=BASE) == BASE + timedelta(days=expected_days)

    def test_update_mastery(self) -> None:
        assert update_mastery(0.0, correct=True) == 0.4  # 按 40% 逼近 1
        assert update_mastery(0.4, correct=True) == 0.64
        assert update_mastery(0.8, correct=False) == 0.4  # 答错减半
        assert update_mastery(5.0, correct=True) == 1.0  # 输入越界先截断

    def _make_record(self, session: FakeSession, user_id: str = USER,
                     review_count: int = 0, mastery: float = 0.0) -> MistakeRecord:
        record = MistakeRecord(
            user_id=user_id, subject="数学", question="解方程 x^2=4",
            wrong_answer="x=4", correct_answer="x=±2", analysis="漏掉负根",
            knowledge_points=["一元二次方程"], mastery=mastery,
            review_count=review_count, next_review_at=BASE,
        )
        session.add(record)
        return record

    async def test_review_correct_advances_schedule(self, fake_session: FakeSession) -> None:
        record = self._make_record(fake_session)
        await fake_session.flush()
        updated = await mistake_service.review_mistake(
            fake_session, str(record.id), USER, correct=True
        )
        assert updated.review_count == 1
        assert updated.mastery == 0.4
        # 第 1 次复习后 → 间隔跳到 2 天
        assert updated.next_review_at > datetime.now(timezone.utc) + timedelta(days=1)

    async def test_review_wrong_resets_to_one_day(self, fake_session: FakeSession) -> None:
        record = self._make_record(fake_session, review_count=3, mastery=0.8)
        await fake_session.flush()
        updated = await mistake_service.review_mistake(
            fake_session, str(record.id), USER, correct=False
        )
        assert updated.review_count == 4
        assert updated.mastery == 0.4
        assert updated.next_review_at < datetime.now(timezone.utc) + timedelta(days=2)

    async def test_review_other_user_returns_none(self, fake_session: FakeSession) -> None:
        record = self._make_record(fake_session)
        await fake_session.flush()
        assert await mistake_service.review_mistake(
            fake_session, str(record.id), "someone-else"
        ) is None


# ---------------------------------------------------------------------------
# SM-2（记忆卡片）
# ---------------------------------------------------------------------------
class TestSm2:
    def test_good_progression(self) -> None:
        """记得：间隔 1 天 → 6 天 → interval*EF 推进。"""
        state = sm2_update(ease_factor=2.5, repetitions=0, interval_days=0, quality=5, now=BASE)
        assert state["interval_days"] == 1 and state["repetitions"] == 1
        assert state["ease_factor"] == 2.6
        assert state["due_date"] == BASE + timedelta(days=1)

        state = sm2_update(state["ease_factor"], state["repetitions"],
                           state["interval_days"], quality=5, now=BASE)
        assert state["interval_days"] == 6 and state["repetitions"] == 2

        state = sm2_update(state["ease_factor"], state["repetitions"],
                           state["interval_days"], quality=5, now=BASE)
        assert state["interval_days"] == round(6 * 2.7) and state["repetitions"] == 3

    def test_again_resets(self) -> None:
        """忘记：repetitions 归零，间隔回到 1 天，EF 下调。"""
        state = sm2_update(ease_factor=2.5, repetitions=3, interval_days=10, quality=1, now=BASE)
        assert state["repetitions"] == 0
        assert state["interval_days"] == 1
        assert state["ease_factor"] < 2.5

    def test_ease_factor_floor(self) -> None:
        """连续忘记，EF 不低于 1.3。"""
        ease = 2.5
        for _ in range(10):
            ease = sm2_update(ease, 0, 1, quality=0, now=BASE)["ease_factor"]
        assert ease == MIN_EASE_FACTOR

    def test_fuzzy_keeps_progressing_slower(self) -> None:
        """模糊（quality=3）：仍推进但 EF 下调。"""
        state = sm2_update(ease_factor=2.5, repetitions=1, interval_days=1, quality=3, now=BASE)
        assert state["repetitions"] == 2
        assert state["interval_days"] == 6
        assert state["ease_factor"] < 2.5

    def _make_card(self, session: FakeSession, user_id: str = USER) -> Flashcard:
        card = Flashcard(user_id=user_id, front=" fronts ", back="backs", deck="default")
        session.add(card)
        return card

    async def test_review_flashcard_updates_fields(self, fake_session: FakeSession) -> None:
        card = self._make_card(fake_session)
        await fake_session.flush()
        updated = await flashcard_service.review_flashcard(
            fake_session, str(card.id), USER, "good"
        )
        assert updated.repetitions == 1
        assert updated.interval_days == 1
        assert updated.ease_factor == 2.6
        assert updated.next_review_at is not None

    async def test_review_flashcard_again(self, fake_session: FakeSession) -> None:
        card = self._make_card(fake_session)
        card.repetitions, card.interval_days = 2, 6
        await fake_session.flush()
        updated = await flashcard_service.review_flashcard(
            fake_session, str(card.id), USER, "again"
        )
        assert updated.repetitions == 0
        assert updated.interval_days == 1

    async def test_review_flashcard_invalid_rating(self, fake_session: FakeSession) -> None:
        card = self._make_card(fake_session)
        await fake_session.flush()
        with pytest.raises(ValueError):
            await flashcard_service.review_flashcard(fake_session, str(card.id), USER, "perfect")

    async def test_review_other_user_returns_none(self, fake_session: FakeSession) -> None:
        card = self._make_card(fake_session)
        await fake_session.flush()
        assert await flashcard_service.review_flashcard(
            fake_session, str(card.id), "someone-else", "good"
        ) is None
