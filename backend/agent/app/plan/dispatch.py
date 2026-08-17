"""Plan 确认后的生成任务投递（增强机制 §2.1 同源约束）。

红线：
1. 大纲未经 confirmed 状态，禁止投递任何 Celery 生成任务 ——
   dispatch_plan_generation 前置状态校验强制（非 confirmed 直接抛 PlanDispatchError）；
2. 生成任务入参必须包含确认时的大纲快照 outline_snapshot（深拷贝，投递后大纲
   再改不影响已投递任务）；Worker 侧（app/tasks/plan_tasks.py）缺快照拒收；
3. 生成内容以大纲为骨架展开（Worker/生成流水线只按 snapshot 展开，禁止变更结构）。
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Optional

from app.plan.models import PlanDocument
from app.plan.state_machine import PlanStatus

logger = logging.getLogger(__name__)

# 计划驱动生成的统一 Celery 任务名（Worker 实现在 app/tasks/plan_tasks.py）
PLAN_GENERATION_TASK = "agent.plan.generate"

# plan skill_code → 内容生成记录创建方式（确认时先建行，Worker 按 record_id 渲染）
_SKILL_TO_AGENT = {
    "ppt": "ppt_agent",
    "lesson_plan": "lesson_plan_agent",
    "exam_paper": "exam_paper_agent",
    "analysis": "learning_analysis_agent",
}


class PlanDispatchError(RuntimeError):
    """未满足投递前置条件（状态非 confirmed / 快照缺失）。"""


def build_generation_payload(
    plan: PlanDocument, *, record_id: Optional[str] = None
) -> dict[str, Any]:
    """构造 Celery 入参：outline_snapshot 为确认时刻大纲的深拷贝（同源约束）。"""
    snapshot = copy.deepcopy(plan.outline)
    if not snapshot:
        raise PlanDispatchError("大纲快照为空，禁止投递生成任务")
    payload: dict[str, Any] = {
        "plan_id": str(plan.id),
        "user_id": str(plan.user_id) if plan.user_id is not None else None,
        "skill_code": plan.skill_code,
        "plan_version": int(plan.version),
        "outline_snapshot": snapshot,
    }
    if record_id:
        payload["record_id"] = record_id
    return payload


def dispatch_plan_generation(
    deps: Any, plan: PlanDocument, *, record_id: Optional[str] = None
) -> dict[str, Any]:
    """投递生成任务。前置状态校验：非 confirmed 一律拒绝（红线 1，代码级强制）。"""
    if plan.status != PlanStatus.CONFIRMED.value:
        raise PlanDispatchError(
            f"大纲未 confirmed（当前 {plan.status}），禁止投递生成任务"
        )
    payload = build_generation_payload(plan, record_id=record_id)
    deps.celery_send(PLAN_GENERATION_TASK, payload)
    return payload


async def create_generation_record(
    session: Any, user_id: str, plan: PlanDocument
) -> Optional[str]:
    """确认后先建生成记录行（与 sub_agent_dispatch 同一套 content_gen/plan_service）。

    生成参数取 outline["params"]（大纲阶段收集的结构化表单参数）；
    study_plan 走 plan_service.create_plan，其余四个走 content_gen.create_*。
    参数不足抛 ValidationError，由 API 层转 422。
    """
    from app import schemas
    from app.services import content_gen, plan_service
    from app.subagents.celery_bridge import _build_content_request

    params = dict((plan.outline or {}).get("params") or {})
    if plan.skill_code == "study_plan":
        req = schemas.PlanCreateRequest(
            goal_description=str(
                params.get("goal_description") or (plan.outline or {}).get("title") or "学习计划"
            ),
            current_level=str(params.get("current_level") or "未评估"),
            days=int(params.get("days") or 7),
        )
        record = await plan_service.create_plan(session, user_id, req)
        return str(getattr(record, "id", "") or "") or None

    agent = _SKILL_TO_AGENT.get(plan.skill_code)
    if agent is None:
        return None
    request = _build_content_request(agent, params)
    create_name = {
        "ppt_agent": "create_presentation",
        "lesson_plan_agent": "create_lesson_plan",
        "exam_paper_agent": "create_exam_paper",
        "learning_analysis_agent": "create_analysis",
    }[agent]
    record = await getattr(content_gen, create_name)(session, user_id, request)
    return str(getattr(record, "id", "") or "") or None
