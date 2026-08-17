"""GET /v1/skills/toolbar：工具栏 Skill 下发（服务端按身份过滤 + 排序）。

契约（设计文档 §3.7）：返回该身份可见、按 sort_order 排序的 Skill 列表；
前端工具栏折叠态前 4 个、展开态网格均以此为准。身份过滤只在服务端完成。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app import deps as app_deps
from app.skills.loader import SkillError, load_skills, toolbar_for_role

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
) -> dict[str, Any]:
    """按身份下发工具栏 Skill 列表。"""
    try:
        skills = toolbar_for_role(list(_builtin_skills()), role)
    except SkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"role": role, "skills": skills}
