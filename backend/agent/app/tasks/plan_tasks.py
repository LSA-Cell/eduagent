"""Plan 驱动生成任务（增强机制 §2.1 同源约束的 Worker 端强制）。

红线：
- 入参缺 outline_snapshot → Worker 拒收（抛 PlanTaskRejected，任务失败可监控）；
- plan 当前状态非 confirmed → 拒收（生成前置状态在 Worker 侧再校验一次，
  防重复投递/过期令牌绕过）；
- 生成以大纲快照为骨架：本任务只负责契约校验与状态机推进
  （confirmed → generating → completed），实际内容渲染委托既有
  gen_tasks 各技能流水线（record_id 由确认端点建行后随入参携带）。
"""

from __future__ import annotations

from typing import Any, Optional

from eduagent_shared import db
from eduagent_shared.logging import get_logger

from app.plan.manager import PlanManager
from app.plan.models import PlanDocument
from app.plan.state_machine import PlanStatus
from app.tasks.celery_app import celery_app, run_async

log = get_logger("agent.tasks.plan")


class PlanTaskRejected(RuntimeError):
    """非法生成任务（缺快照 / 状态非法），Worker 拒收。"""


_SKILL_RUNNERS = {
    "ppt": "run_generate_presentation",
    "lesson_plan": "run_generate_lesson_plan",
    "exam_paper": "run_generate_exam_paper",
    "analysis": "run_generate_analysis",
    "study_plan": "run_generate_plan_schedule",
}


async def run_generate_from_plan(
    plan_id: str,
    *,
    outline_snapshot: Optional[dict[str, Any]] = None,
    record_id: Optional[str] = None,
    skill_code: Optional[str] = None,
    session_factory: Any = None,
    llm: Any = None,
    docgen: Any = None,
) -> dict[str, Any]:
    """Plan 生成 Worker 核心（测试直接调用；Celery 入口见下方 task）。"""
    # 红线 2：生成任务入参缺失 outline_snapshot 视为非法任务，Worker 必须拒收
    if not outline_snapshot:
        raise PlanTaskRejected(
            f"plan 生成任务缺少 outline_snapshot（plan_id={plan_id}），Worker 拒收"
        )
    factory = session_factory or db.get_session_factory()
    from app.services.llm_client import get_llm_client
    from app.services.docgen_client import get_docgen_client
    from eduagent_shared.config import get_settings

    manager = PlanManager()
    async with factory() as session:
        import uuid as _uuid

        try:
            plan_pk = _uuid.UUID(str(plan_id))
        except (ValueError, AttributeError, TypeError):
            raise PlanTaskRejected(f"plan_id 非法: {plan_id!r}") from None
        plan = await session.get(PlanDocument, plan_pk)
        if plan is None:
            raise PlanTaskRejected(f"plan 不存在: {plan_id}")
        # 红线 1（Worker 侧复核）：非 confirmed 状态禁止生成
        if plan.status != PlanStatus.CONFIRMED.value:
            raise PlanTaskRejected(
                f"plan 状态为 {plan.status}（非 confirmed），Worker 拒收生成任务"
            )
        await manager.mark_generating(plan)
        await session.flush()

        result: dict[str, Any]
        code = skill_code or plan.skill_code
        if record_id:
            # 委托既有生成流水线（以 outline_snapshot 为骨架展开，禁止变更结构）
            from app.tasks import gen_tasks

            settings = get_settings()
            runner = getattr(gen_tasks, _SKILL_RUNNERS[code])
            kwargs: dict[str, Any] = {"session_factory": factory, "llm": llm}
            if code != "study_plan":
                kwargs["docgen"] = docgen
            if llm is None:
                kwargs["llm"] = get_llm_client(settings)
            if code != "study_plan" and docgen is None:
                kwargs["docgen"] = get_docgen_client(settings)
            result = await runner(record_id, **kwargs)
        else:
            # 无生成记录（契约校验/测试路径）：仅推进状态机
            result = {"status": "no_record", "outline_snapshot": outline_snapshot}

        await manager.mark_completed(plan)
        await session.commit()
        log.info("plan_generated", plan_id=plan_id, skill=code)
        return {"plan_id": plan_id, "status": "completed", "skill_code": code, "result": result}


@celery_app.task(name="agent.plan.generate")
def generate_from_plan(
    plan_id: str,
    outline_snapshot: Optional[dict[str, Any]] = None,
    record_id: Optional[str] = None,
    skill_code: Optional[str] = None,
    user_id: Optional[str] = None,
    plan_version: Optional[int] = None,
) -> dict[str, Any]:
    """Plan 生成（Celery 入口）。"""
    return run_async(
        run_generate_from_plan(
            plan_id,
            outline_snapshot=outline_snapshot,
            record_id=record_id,
            skill_code=skill_code,
        )
    )
