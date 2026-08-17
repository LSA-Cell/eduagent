"""GET /v1/skills/toolbar：工具栏 Skill 下发（服务端按身份过滤 + 排序）。

契约（设计文档 §3.7）：返回该身份可见、按 sort_order 排序的 Skill 列表；
前端工具栏折叠态前 4 个、展开态网格均以此为准。身份过滤只在服务端完成。

Wave D1（§3.3）：可选参数 rank_by_denial=true 时按 denial_counters
重排/标注（consecutive≥3 沉底、total≥20 加 default_enabled=False）；
只影响推荐排序与默认值，读计数失败静默回退原排序（fail-soft）。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app import deps as app_deps
from app.skills.loader import SkillError, load_skills, toolbar_for_role

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1",
    tags=["skills"],
    dependencies=[Depends(app_deps.require_internal_auth)],
)


@lru_cache
def _builtin_skills() -> tuple[Any, ...]:
    """内置 YAML 基线（进程内缓存；DB 热更新接入后改为 load_skills_async）。"""
    return tuple(load_skills())


@router.get("/skills/toolbar")
async def skills_toolbar(
    role: str = Query(default="student"),
    rank_by_denial: bool = Query(default=False),
    x_user_id: str = Header(default="", alias="X-User-Id"),
) -> dict[str, Any]:
    """按身份下发工具栏 Skill 列表；可选按拒绝计数重排/标注（仅排序建议）。"""
    try:
        skills = toolbar_for_role(list(_builtin_skills()), role)
    except SkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if rank_by_denial and x_user_id:
        skills = await _apply_denial_ranking(skills, x_user_id)
    return {"role": role, "skills": skills}


async def _apply_denial_ranking(skills: list[dict[str, Any]], user_id: str) -> list[dict[str, Any]]:
    """denial_counters 排序挂接点（最小集成）；读计数失败回退原排序。"""
    try:
        from eduagent_shared.db import get_session_factory

        from app.denial.counters import load_counters
        from app.denial.ranking import apply_denial_ranking

        async with get_session_factory()() as session:
            counters = await load_counters(session, user_id)
        return apply_denial_ranking(skills, counters)
    except Exception:  # noqa: BLE001 - 计数不可用不影响正常下发
        logger.warning("denial 排序降级为默认顺序", exc_info=True)
        return skills
