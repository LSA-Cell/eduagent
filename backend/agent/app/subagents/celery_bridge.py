"""异步子 Agent Celery 桥接：投递既有 gen_tasks 或新任务，立即返回受理卡片。

流程（设计文档 §3.3）：
    定义(async_via=celery) → 计算幂等 task_id → dedup 去重 →
    内容生成类先建行（content_gen.create_*，与教师路由同一套）→
    deps.celery_send 投递 → 立即返回"任务受理卡片" ToolResult（含 task_id）。

幂等红线：
- task_id = sha256(agent|user_id|task|params) 稳定哈希（同参数重试天然去重）；
  params 显式带 task_id 时优先采用（调用方/客户端重试场景）；
- 派发键 ``dispatch:{task_id}`` 去重：重复派发返回 duplicate 卡片，不再投递；
- 完成键 ``complete:{task_id}`` 由 worker 侧（app/tasks/subagent_tasks.py）去重，
  重复完成事件禁止重复写库；
- 投递失败释放 dispatch 键，允许调用方重试。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Optional

from pydantic import ValidationError

from app.subagents.loader import SubAgentDefinition
from app.tools.base import ToolContext, ToolResult

logger = logging.getLogger(__name__)

DEDUP_TTL_S = 3600  # 去重窗口（1 小时，覆盖任务全生命周期）

# 内容生成类子 Agent →（记录创建所需参数字段、create_* 函数名、Celery 任务名）
_CONTENT_AGENTS: dict[str, tuple[str, str]] = {
    "ppt_agent": ("create_presentation", "agent.gen.presentation"),
    "lesson_plan_agent": ("create_lesson_plan", "agent.gen.lesson_plan"),
    "exam_paper_agent": ("create_exam_paper", "agent.gen.exam_paper"),
    "learning_analysis_agent": ("create_analysis", "agent.gen.analysis"),
}
# 其余 celery 子 Agent 走通用子 Agent worker 任务
GENERIC_SUBAGENT_TASK = "agent.subagent.run"


class TaskDedup:
    """task_id 去重：Redis（SET NX EX）可用时用 Redis，否则进程内内存集合。

    claim 返回 True=首次见到（放行）；False=窗口内已存在（去重拦截）。
    """

    def __init__(self, redis: Any = None, *, ttl_s: int = DEDUP_TTL_S) -> None:
        self._redis = redis
        self._ttl_s = ttl_s
        self._memory: dict[str, float] = {}

    def _purge(self) -> None:
        now = time.monotonic()
        for key in [k for k, exp in self._memory.items() if exp <= now]:
            del self._memory[key]

    async def claim(self, key: str, *, ttl_s: Optional[int] = None) -> bool:
        ttl = int(ttl_s or self._ttl_s)
        if self._redis is not None:
            try:
                ok = await self._redis.set(f"agent:dedup:{key}", 1, nx=True, ex=ttl)
                return bool(ok)
            except Exception:  # noqa: BLE001 - Redis 故障降级内存去重
                logger.warning("task dedup redis 失败，降级内存", exc_info=True)
        self._purge()
        if key in self._memory:
            return False
        self._memory[key] = time.monotonic() + ttl
        return True

    async def release(self, key: str) -> None:
        """释放去重键（投递失败时调用，允许重试）。"""
        if self._redis is not None:
            try:
                await self._redis.delete(f"agent:dedup:{key}")
            except Exception:  # noqa: BLE001
                logger.warning("task dedup release 失败", exc_info=True)
        self._memory.pop(key, None)


def make_task_id(agent: str, user_id: str, task: str, params: dict[str, Any]) -> str:
    """稳定幂等键：同 agent + 同用户 + 同参数 → 同 task_id。"""
    canonical = json.dumps(
        {"task": task, "params": params}, ensure_ascii=False, sort_keys=True, default=str
    )
    digest = hashlib.sha256(f"{agent}|{user_id}|{canonical}".encode("utf-8")).hexdigest()
    return f"sa-{digest[:32]}"


def _build_content_request(agent: str, params: dict[str, Any]) -> Any:
    """params → content_gen.create_* 所需的请求对象（复用 API 层 Pydantic 校验）。"""
    from app import schemas

    if agent == "ppt_agent":
        return schemas.PresentationCreateRequest(
            topic=str(params.get("topic") or params.get("course_name") or ""),
            grade=str(params.get("grade") or params.get("grade_level") or ""),
            subject=str(params.get("subject") or ""),
            style=str(params.get("style") or "default"),
            pages=int(params.get("pages") or 10),
        )
    if agent == "lesson_plan_agent":
        req = schemas.LessonPlanCreateRequest(
            course_name=str(params.get("course_name") or params.get("topic") or ""),
            grade=str(params.get("grade") or params.get("grade_level") or ""),
            duration=int(params.get("duration") or 45),
            objectives=[str(o) for o in params.get("objectives") or []],
            template_type=str(params.get("template_type") or "standard"),
        )
        # LessonPlanCreateRequest 无 subject 字段；content_gen 用 hasattr 探测，
        # model_copy(update=...) 不做校验，额外键落入实例 __dict__
        subject = params.get("subject")
        return req.model_copy(update={"subject": subject}) if subject else req
    if agent == "exam_paper_agent":
        return schemas.ExamPaperCreateRequest(
            grade=str(params.get("grade") or params.get("grade_level") or ""),
            subject=str(params.get("subject") or ""),
            question_types=[str(t) for t in params.get("question_types") or []],
            difficulty=str(params.get("difficulty") or "medium"),
            count=int(params.get("count") or 10),
        )
    if agent == "learning_analysis_agent":
        return schemas.AnalysisCreateRequest(
            class_name=str(params.get("class_name") or ""),
            analysis_type=str(params.get("analysis_type") or "overview"),
            data_source=dict(params.get("data_source") or {}),
        )
    raise ValueError(f"非内容生成类子 Agent: {agent}")


async def dispatch_async(
    definition: SubAgentDefinition,
    *,
    task: str,
    params: Optional[dict[str, Any]] = None,
    subject: Optional[str],
    deps: Any,
    ctx: ToolContext,
) -> ToolResult:
    """投递异步子 Agent，返回任务受理卡片（本函数不抛异常）。"""
    params = dict(params or {})
    dedup: TaskDedup = getattr(deps, "task_dedup", None) or TaskDedup()
    task_id = str(params.get("task_id") or make_task_id(definition.name, ctx.user_id, task, params))

    if not await dedup.claim(f"dispatch:{task_id}"):
        return ToolResult(
            content=f"任务已受理（task_id={task_id}），重复请求已去重，请勿重复提交。",
            card_type=definition.card_type,
            card_data={"task_id": task_id, "status": "duplicate", "agent": definition.name},
        )

    try:
        if definition.name in _CONTENT_AGENTS:
            create_name, task_name = _CONTENT_AGENTS[definition.name]
            from app.services import content_gen

            request = _build_content_request(definition.name, params)
            async with deps.session_factory() as session:
                record = await getattr(content_gen, create_name)(session, ctx.user_id, request)
                await session.commit()
            record_id = str(getattr(record, "id", ""))
            payload: dict[str, Any] = {"record_id": record_id}
            card_extra: dict[str, Any] = {"record_id": record_id}
        else:
            task_name = GENERIC_SUBAGENT_TASK
            payload = {
                "task_id": task_id,
                "agent": definition.name,
                "user_id": ctx.user_id,
                "task": task,
                "params": params,
                "subject": subject,
                "session_id": ctx.session_id,
            }
            card_extra = {}
        deps.celery_send(task_name, payload)
    except ValidationError as exc:
        await dedup.release(f"dispatch:{task_id}")
        missing = ", ".join(
            ".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()[:3]
        )
        return ToolResult(
            content=f"子 Agent {definition.name} 参数不足或非法（{missing}），请补充后重试。",
            card_type=definition.card_type,
            card_data={"agent": definition.name, "status": "invalid_params", "retryable": True},
        )
    except Exception as exc:  # noqa: BLE001 - 结构化回注，禁止冒泡中断父 Loop
        logger.warning("子 Agent %s 派发失败", definition.name, exc_info=True)
        await dedup.release(f"dispatch:{task_id}")
        return ToolResult(
            content=f"子 Agent {definition.name} 派发失败：{type(exc).__name__}（可重试）。",
            card_type=definition.card_type,
            card_data={"agent": definition.name, "status": "failed", "retryable": True},
        )

    card_data = {
        "task_id": task_id,
        "status": "accepted",
        "agent": definition.name,
        **card_extra,
    }
    return ToolResult(
        content=(
            f"已受理{definition.description}任务（task_id={task_id}）。"
            "任务在后台执行，完成后会推送结果与文件；请告诉用户无需等待。"
        ),
        card_type=definition.card_type,
        card_data=card_data,
    )
