"""学习计划引擎：LLM 生成日程 → study_plans 表，支持打卡。

字段映射（对齐 shared.models.StudyPlan）：
goal_description→goal，days→start/end_date，generated_schedule 与
completed_days 存 plan_content 字典（"schedule"/"completed_days" 键），
打卡进度同步 progress 列（0~1）。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from eduagent_shared.models import StudyPlan
from eduagent_shared.logging import get_logger

from app import prompts
from app.services.content_gen import PlanDay
from app.services.llm_client import LLMClient

log = get_logger("agent.plan_service")
_FAILURE_MESSAGE = "学习计划生成失败，请稍后重试"


async def create_plan(session: Any, user_id: str, req: Any) -> Any:
    """创建学习计划记录（status=generating），日程由 Celery 任务填充。"""
    today = date.today()
    plan = StudyPlan(
        user_id=user_id,
        title=req.goal_description[:50],
        goal=req.goal_description,
        start_date=today,
        end_date=today + timedelta(days=req.days),
        status="generating",
        plan_content={
            "current_level": req.current_level,
            "days": req.days,
            "schedule": [],
            "completed_days": [],
        },
        progress=0.0,
    )
    session.add(plan)
    await session.flush()
    return plan


async def run_plan_generation(session: Any, llm: LLMClient, plan: Any) -> dict[str, Any]:
    """LLM 生成 [{day, task, duration}] 并写入 plan_content.schedule。"""
    content = dict(plan.plan_content or {})
    try:
        raw = await llm.chat_json(
            "你是学习规划师，输出严格 JSON 数组。",
            prompts.PLAN_SCHEDULE_PROMPT.format(
                goal_description=plan.goal or plan.title,
                current_level=content.get("current_level", "未知"),
                days=content.get("days", 7),
            ),
        )
        if not isinstance(raw, list):
            raise ValueError("LLM 输出不是 JSON 数组")
        schedule = [PlanDay.model_validate(item).model_dump() for item in raw]
        content["schedule"] = schedule
        content.pop("_error", None)
        plan.plan_content = content
        plan.status = "active"
    except Exception as exc:  # noqa: BLE001 - 失败落库供轮询
        content["_error"] = _FAILURE_MESSAGE
        log.warning("plan_generation_failed", error_type=type(exc).__name__)
        plan.plan_content = content
        plan.status = "failed"
    await session.flush()
    return {"id": str(getattr(plan, "id", "")), "status": plan.status}


async def checkin(
    session: Any, plan_id: str, user_id: str, day: int, note: Optional[str] = None
) -> Optional[Any]:
    """任务完成打卡：把 day 加入 completed_days（幂等），并更新 progress。"""
    plan = await session.get(StudyPlan, plan_id)
    if plan is None or str(plan.user_id) != str(user_id):
        return None
    content = dict(plan.plan_content or {})
    completed = list(content.get("completed_days") or [])
    if day not in completed:
        completed.append(day)
        completed.sort()
    content["completed_days"] = completed
    if note:
        notes = dict(content.get("notes") or {})
        notes[str(day)] = note
        content["notes"] = notes
    plan.plan_content = content
    total_days = int(content.get("days") or 0)
    plan.progress = min(1.0, len(completed) / total_days) if total_days > 0 else 0.0
    if total_days > 0 and len(completed) >= total_days:
        plan.status = "completed"
    await session.flush()
    return plan


def plan_to_dict(plan: Any) -> dict[str, Any]:
    """记录 → API DTO（屏蔽 plan_content 存储细节）。"""
    content = dict(plan.plan_content or {})
    return {
        "id": str(plan.id), "title": plan.title,
        "goal_description": plan.goal,
        "current_level": content.get("current_level"),
        "days": content.get("days"),
        "generated_schedule": content.get("schedule") or [],
        "completed_days": content.get("completed_days") or [],
        "progress": plan.progress,
        "status": plan.status,
        "error": _FAILURE_MESSAGE if content.get("_error") else None,
        "start_date": plan.start_date.isoformat() if plan.start_date else None,
        "end_date": plan.end_date.isoformat() if plan.end_date else None,
        "created_at": plan.created_at.isoformat() if getattr(plan, "created_at", None) else None,
    }
