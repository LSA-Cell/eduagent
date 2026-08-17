"""PlanManager：大纲草稿/确认/修改/取消 + confirm_token 签发与校验。

红线落实（增强机制 §2.1）：
- 白名单 skill（ppt/lesson_plan/exam_paper/study_plan/analysis），其余拒绝进入 Plan 流程；
- confirm_token 只存 SHA-256 哈希，绑定 plan_id + user_id（同行校验），30 分钟过期，
  确认成功即失效（一次性），禁止跨用户/跨 plan 复用；
- 修改产生新 plan_version（parent_plan_id 指回旧版本），旧版本保留（置 cancelled +
  令牌作废），禁止原地覆盖；
- 每次状态迁移触发 on_transition 回调（Loop 侧据此推 plan.status SSE 事件）。

存储全部经注入的 session（测试用 FakeSession），管理器本身无状态。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from app.plan.models import PlanDocument
from app.plan.state_machine import PlanStateError, PlanStatus, transition

logger = logging.getLogger(__name__)

# 长内容生成类 Skill 白名单（快问快答类禁止进入 Plan 流程）
PLAN_SKILL_WHITELIST: frozenset[str] = frozenset(
    {"ppt", "lesson_plan", "exam_paper", "study_plan", "analysis"}
)

CONFIRM_TOKEN_TTL_S = 30 * 60  # 30 分钟（规格固定值）


class PlanError(RuntimeError):
    """Plan 流程错误基类。"""


class PlanNotFoundError(PlanError):
    """plan 不存在。"""


class PlanPermissionError(PlanError):
    """跨用户访问（令牌/操作与 plan 归属不一致）。"""


class PlanTokenError(PlanError):
    """confirm_token 非法 / 过期 / 已失效。"""


def _as_uuid(value: Any, *, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise PlanError(f"{field} 不是合法 UUID: {value!r}") from None


def _as_uuid_lenient(value: Any) -> Optional[uuid.UUID]:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _aware(dt: datetime) -> datetime:
    """naive 时间按 UTC 处理（DDL 为 TIMESTAMP 列，ORM 写入 aware）。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def hash_confirm_token(token: str) -> str:
    """confirm_token 的存储形态：SHA-256 hex（64 字符，落 confirm_token_hash）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


OnTransition = Callable[[PlanDocument, PlanStatus, PlanStatus], None]


class PlanManager:
    """Plan 生命周期管理（无状态；方法接收已打开的 session，由调用方 commit）。"""

    def __init__(
        self,
        *,
        token_ttl_s: int = CONFIRM_TOKEN_TTL_S,
        now: Optional[Callable[[], datetime]] = None,
        on_transition: Optional[OnTransition] = None,
    ) -> None:
        self._token_ttl_s = token_ttl_s
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._on_transition = on_transition

    # ---- 内部 ---------------------------------------------------------------
    def _apply(self, plan: PlanDocument, target: PlanStatus) -> None:
        old = transition(plan, target)
        if target is PlanStatus.CONFIRMED:
            plan.confirmed_at = self._now()
        if self._on_transition is not None:
            try:
                self._on_transition(plan, old, target)
            except Exception:  # noqa: BLE001 - 事件回调失败不影响状态迁移
                logger.warning("plan.status 事件回调失败", exc_info=True)

    async def _load(self, session: Any, plan_id: Any) -> PlanDocument:
        plan = await session.get(PlanDocument, _as_uuid(plan_id, field="plan_id"))
        if plan is None:
            raise PlanNotFoundError(f"plan 不存在: {plan_id}")
        return plan

    @staticmethod
    def _check_owner(plan: PlanDocument, user_id: Any) -> None:
        if str(plan.user_id) != str(_as_uuid(user_id, field="user_id")):
            raise PlanPermissionError("无权操作他人 plan")

    def _issue_token(self, plan: PlanDocument) -> str:
        """签发一次性确认令牌：原文仅本次返回，落库只存哈希 + 过期时刻。"""
        token = secrets.token_hex(32)
        plan.confirm_token_hash = hash_confirm_token(token)
        plan.confirm_expires_at = self._now() + timedelta(seconds=self._token_ttl_s)
        return token

    def _expire_if_needed(self, plan: PlanDocument) -> None:
        """awaiting_confirm 超期 → expired（懒过期：操作触发时判定）。"""
        if (
            plan.status == PlanStatus.AWAITING_CONFIRM.value
            and plan.confirm_expires_at is not None
            and _aware(plan.confirm_expires_at) <= self._now()
        ):
            self._apply(plan, PlanStatus.EXPIRED)
            plan.confirm_token_hash = None  # 过期令牌一律作废

    # ---- 大纲阶段 ------------------------------------------------------------
    async def draft_and_submit(
        self,
        session: Any,
        *,
        user_id: Any,
        session_id: Any,
        skill_code: str,
        outline: dict[str, Any],
    ) -> tuple[PlanDocument, str]:
        """创建大纲草稿并提交确认：drafting → awaiting_confirm，返回 (plan, token)。"""
        if skill_code not in PLAN_SKILL_WHITELIST:
            raise PlanError(
                f"skill {skill_code!r} 不在 Plan Mode 白名单内"
                f"（{sorted(PLAN_SKILL_WHITELIST)}）；长内容生成类才可两段式生成。"
            )
        if not isinstance(outline, dict) or not outline:
            raise PlanError("outline 必须为非空 JSON 对象")
        plan = PlanDocument(
            session_id=_as_uuid_lenient(session_id),
            user_id=_as_uuid(user_id, field="user_id"),
            skill_code=skill_code,
            version=1,
            outline=outline,
            status=PlanStatus.DRAFTING.value,
        )
        session.add(plan)
        await session.flush()  # 分配 id
        self._apply(plan, PlanStatus.AWAITING_CONFIRM)  # 红线：每次迁移推 plan.status
        token = self._issue_token(plan)
        await session.flush()
        return plan, token

    # ---- 确认协议 ------------------------------------------------------------
    async def confirm(
        self, session: Any, plan_id: Any, *, user_id: Any, token: str
    ) -> PlanDocument:
        """确认大纲：校验归属 + 令牌（哈希比对 + 未过期 + 一次性）→ confirmed。"""
        plan = await self._load(session, plan_id)
        self._check_owner(plan, user_id)  # 红线：禁止跨用户
        self._expire_if_needed(plan)
        if plan.status == PlanStatus.EXPIRED.value:
            raise PlanTokenError("确认已超时（30 分钟），大纲已过期，请重新生成")
        if plan.status != PlanStatus.AWAITING_CONFIRM.value:
            raise PlanStateError(f"当前状态 {plan.status} 不允许确认")
        if not token or not plan.confirm_token_hash:
            raise PlanTokenError("缺少有效确认令牌")
        if hash_confirm_token(token) != plan.confirm_token_hash:
            raise PlanTokenError("确认令牌与本大纲不匹配")  # 跨 plan 复用必然哈希不符
        plan.confirm_token_hash = None  # 一次性：确认成功即失效
        self._apply(plan, PlanStatus.CONFIRMED)
        await session.flush()
        return plan

    async def revise(
        self,
        session: Any,
        plan_id: Any,
        *,
        user_id: Any,
        outline: dict[str, Any],
    ) -> tuple[PlanDocument, str]:
        """修改大纲：旧版本保留（cancelled + 令牌作废），产生新 plan_version。"""
        old = await self._load(session, plan_id)
        self._check_owner(old, user_id)
        self._expire_if_needed(old)
        if old.status not in (
            PlanStatus.AWAITING_CONFIRM.value,
            PlanStatus.DRAFTING.value,
        ):
            raise PlanStateError(f"当前状态 {old.status} 不允许修改")
        if not isinstance(outline, dict) or not outline:
            raise PlanError("outline 必须为非空 JSON 对象")
        old.confirm_token_hash = None  # 旧版本令牌作废，禁止确认过期版本
        self._apply(old, PlanStatus.CANCELLED)
        new_plan = PlanDocument(
            session_id=old.session_id,
            user_id=old.user_id,
            skill_code=old.skill_code,
            version=int(old.version) + 1,
            outline=outline,
            status=PlanStatus.DRAFTING.value,
            parent_plan_id=old.id,  # 红线：新版本指回旧版本
        )
        session.add(new_plan)
        await session.flush()
        self._apply(new_plan, PlanStatus.AWAITING_CONFIRM)
        token = self._issue_token(new_plan)
        await session.flush()
        return new_plan, token

    async def cancel(self, session: Any, plan_id: Any, *, user_id: Any) -> PlanDocument:
        """取消大纲（drafting/awaiting_confirm/confirmed/generating → cancelled）。"""
        plan = await self._load(session, plan_id)
        self._check_owner(plan, user_id)
        self._expire_if_needed(plan)
        self._apply(plan, PlanStatus.CANCELLED)
        plan.confirm_token_hash = None
        await session.flush()
        return plan

    # ---- 生成阶段状态推进（Worker 侧调用） ------------------------------------
    async def mark_generating(self, plan: PlanDocument) -> None:
        self._apply(plan, PlanStatus.GENERATING)

    async def mark_completed(self, plan: PlanDocument) -> None:
        self._apply(plan, PlanStatus.COMPLETED)
