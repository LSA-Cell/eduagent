"""自动化 Hooks 与降级路径测试（§5.4）。

覆盖红线：事件触发异步派发（Celery eager 下断言动作被调）/ 主流程不被
Hook 异常影响（fail-soft）/ 降级链明示文案（禁止静默降级）。
"""

from __future__ import annotations

import uuid

import pytest

from app.hooks.dispatcher import dispatch_event
from app.hooks.fallbacks import (
    FALLBACK_CHAINS,
    has_fallback_chain,
    next_mode,
    resolve_fallback,
)
from app.hooks.models import AutomationHook
from app.hooks.registry import HookRegistry
from tests.conftest import FakeSession, as_factory


def _seed_hook(session: FakeSession, event: str, action: str, *, active: bool = True) -> None:
    session.add(AutomationHook(owner_id=uuid.uuid4(), event=event, action=action,
                               config={"channel": "app_push"}, is_active=active))


class TestDispatch:
    async def test_event_triggers_async_action(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """事件触发 → automation_hooks 配置 → Celery eager 下动作真实被调。"""
        import asyncio

        import app.hooks.tasks as hook_tasks

        called: list[dict] = []

        async def spy_action(**kwargs):
            called.append(kwargs)
            return {"ok": True}

        # eager 在已运行事件循环内不能 asyncio.run：shim run_async 为当前
        # loop 的 create_task（生产 worker 为同步上下文，走真实 run_async）
        def loop_driven(coro):
            return asyncio.get_running_loop().create_task(coro)

        monkeypatch.setattr(hook_tasks, "run_hook_action", spy_action)
        monkeypatch.setattr(hook_tasks, "run_async", loop_driven)

        session = FakeSession()
        _seed_hook(session, "mistake.created", "notify_parent")
        _seed_hook(session, "mistake.created", "notify_teacher")
        _seed_hook(session, "mistake.created", "notify_parent", active=False)  # 停用不派发
        _seed_hook(session, "plan.confirmed", "notify_teacher")  # 其它事件不派发
        await session.flush()
        registry = HookRegistry(as_factory(session))

        result = await dispatch_event(
            "mistake.created", {"user_id": "u1", "mistake_id": "m1"},
            registry=registry,
        )
        assert result["dispatched"] == 2
        await asyncio.sleep(0)  # 让 eager 任务协程跑完
        await asyncio.sleep(0)
        assert [c["action"] for c in called] == ["notify_parent", "notify_teacher"]
        assert all(c["event"] == "mistake.created" for c in called)
        assert called[0]["payload"]["mistake_id"] == "m1"

    async def test_no_hooks_noop(self) -> None:
        session = FakeSession()
        await session.flush()
        result = await dispatch_event(
            "dream.completed", {"user_id": "u1"}, registry=HookRegistry(as_factory(session))
        )
        assert result["dispatched"] == 0

    async def test_dispatch_fail_soft_on_db_error(self) -> None:
        """红线：Hook 异常不影响主流程——DB 故障返回 error 标记而不抛出。"""

        class BrokenCM:
            async def __aenter__(self):
                raise RuntimeError("db down")

            async def __aexit__(self, *exc):
                return False

        result = await dispatch_event(
            "mistake.created", {"user_id": "u1"}, session_factory=lambda: BrokenCM()
        )
        assert result["dispatched"] == 0  # 加载失败 fail-soft：不派发、不抛出

    async def test_unknown_event_and_action_filtered(self) -> None:
        """协议外事件/动作配置不进缓存（封闭枚举语义）。"""
        session = FakeSession()
        _seed_hook(session, "evil.event", "notify_parent")
        _seed_hook(session, "mistake.created", "shell_exec")
        await session.flush()
        registry = HookRegistry(as_factory(session))
        await registry.refresh()
        assert registry.hooks_for("evil.event") == []
        assert registry.hooks_for("mistake.created") == []

    async def test_mistake_extract_attach_point(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """挂接点：错题提取命中后触发 mistake.created（fail-soft 不影响提取结果）。"""
        from app.services.llm_client import ChatResult
        from app.tasks import mistake_tasks
        from tests.conftest import FakeLLM

        dispatched: list[dict] = []

        async def spy_dispatch(event, payload, **kwargs):
            dispatched.append({"event": event, "payload": payload})
            return {"dispatched": 1}

        monkeypatch.setattr(
            "app.hooks.dispatcher.dispatch_event", spy_dispatch
        )

        user_id = str(uuid.uuid4())
        chat_session_id = str(uuid.uuid4())
        session = FakeSession()

        # chat_service.load_recent_messages 走 select(Message)（FakeSession 注册表）
        from eduagent_shared.models import Message

        session.add(Message(id=uuid.uuid4(), session_id=uuid.UUID(chat_session_id),
                            role="user", content="1+1=3"))
        await session.flush()
        llm = FakeLLM(json_result={
            "is_mistake": True, "question": "1+1=?", "user_answer": "3",
            "correct_answer": "2", "analysis": "算错", "knowledge_tags": ["加法"],
        })
        result = await mistake_tasks.run_extract_mistake(
            user_id, chat_session_id, session_factory=as_factory(session), llm=llm
        )
        assert result["created"] is True
        assert dispatched and dispatched[0]["event"] == "mistake.created"
        assert dispatched[0]["payload"]["mistake_id"] == result["mistake_id"]

        # Hook 派发异常 → 提取结果不受影响（fail-soft）
        async def boom(event, payload, **kwargs):
            raise RuntimeError("hook channel down")

        monkeypatch.setattr("app.hooks.dispatcher.dispatch_event", boom)
        result2 = await mistake_tasks.run_extract_mistake(
            user_id, chat_session_id, session_factory=as_factory(session), llm=llm
        )
        assert result2["created"] is True

    async def test_dream_completed_hook_triggered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """挂接点：dream 整合完成后触发 dream.completed（fail-soft）。"""
        from datetime import date

        from app.dream.locks import InMemoryDreamLock
        from app.dream.tasks import run_dream_for_user

        dispatched: list[str] = []

        async def spy_dispatch(event, payload, **kwargs):
            dispatched.append(event)
            return {"dispatched": 0}

        monkeypatch.setattr("app.hooks.dispatcher.dispatch_event", spy_dispatch)
        session = FakeSession()
        result = await run_dream_for_user(
            str(uuid.uuid4()), day=date(2026, 8, 16).isoformat(),
            session_factory=as_factory(session), llm=None,
            lock=InMemoryDreamLock(),
        )
        assert result["status"] == "completed"
        assert dispatched == ["dream.completed"]


class TestFallbacks:
    def test_chains_declared(self) -> None:
        """红线：多模态功能必须声明降级链（首批三条链路齐全）。"""
        assert FALLBACK_CHAINS["voice_practice"] == ["voice", "text"]
        assert FALLBACK_CHAINS["photo_search"] == ["camera", "album_upload", "manual_input"]
        assert FALLBACK_CHAINS["ocr_grading"] == ["ocr", "manual_select"]
        for feature in ("voice_practice", "photo_search", "ocr_grading"):
            assert has_fallback_chain(feature) is True

    def test_explicit_notice_on_switch(self) -> None:
        """降级切换返回明示文案结构（"已切换 X 模式"，禁止静默降级）。"""
        result = resolve_fallback("voice_practice", "voice")
        assert result["degraded"] is True
        assert result["from_mode"] == "voice" and result["to_mode"] == "text"
        assert result["notice"] == "语音服务暂不可用，已切换文本模式"

        second = resolve_fallback("photo_search", "camera")
        assert second["to_mode"] == "album_upload"
        assert "已切换" in second["notice"] and "相册上传" in second["notice"]
        third = resolve_fallback("photo_search", "album_upload")
        assert third["to_mode"] == "manual_input"

    def test_chain_tail_still_explicit(self) -> None:
        """链尾无更低模式：也必须明示（"暂不可用，请稍后再试"），不得静默。"""
        result = resolve_fallback("ocr_grading", "manual_select")
        assert result["degraded"] is False and result["to_mode"] is None
        assert "暂不可用" in result["notice"] and "请稍后再试" in result["notice"]

    def test_unknown_feature_and_mode(self) -> None:
        assert next_mode("unknown_feature", "voice") is None
        assert has_fallback_chain("unknown_feature") is False

    def test_chains_overridable_via_config(self) -> None:
        """降级链配置化：运营可注入覆盖链，禁止改代码散写。"""
        custom = {"voice_practice": ["voice", "text", "manual_input"]}
        assert next_mode("voice_practice", "text", chains=custom) == "manual_input"
