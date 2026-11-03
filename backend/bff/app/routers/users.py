"""用户路由：资料查询/更新、身份模式切换、教师认证提交。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from eduagent_shared.logging import get_logger

from ..deps import get_current_user, get_user_store
from ..errors import AppError
from ..schemas import (
    ModeUpdate,
    ProfileUpdate,
    TeacherCertificationRequest,
    UserOut,
)
from ..services.store import UserStore

router = APIRouter(prefix="/users", tags=["users"])
log = get_logger("bff.users")


@router.get("/me", response_model=UserOut)
async def get_me(user=Depends(get_current_user)) -> UserOut:
    """查询当前用户资料。"""
    return UserOut.model_validate(user)


@router.put("/me", response_model=UserOut)
async def update_me(
    payload: ProfileUpdate,
    user=Depends(get_current_user),
    user_store: UserStore = Depends(get_user_store),
) -> UserOut:
    """更新资料（grade_level / default_subject，仅提交非空字段）。"""
    updated = await user_store.update_profile(
        user.id,
        grade_level=payload.grade_level,
        default_subject=payload.default_subject,
    )
    if updated is None:
        raise AppError(404, "user_not_found", "用户不存在")
    return UserOut.model_validate(updated)


@router.put("/me/mode", response_model=UserOut)
async def set_mode(
    payload: ModeUpdate,
    user=Depends(get_current_user),
    user_store: UserStore = Depends(get_user_store),
) -> UserOut:
    """切换身份模式：student / teacher / parent（存 users.user_mode 字段）。"""
    updated = await user_store.set_mode(user.id, payload.mode)
    if updated is None:
        raise AppError(404, "user_not_found", "用户不存在")
    return UserOut.model_validate(updated)


@router.post("/me/teacher-certification", status_code=202)
async def submit_teacher_certification(
    payload: TeacherCertificationRequest,
    user=Depends(get_current_user),
) -> dict[str, str]:
    """教师认证材料提交（占位实现，返回 202 受理）。

    文件由移动端先经预签名上传至 MinIO；当前只返回受理状态，不持久化材料。
    审核工作流（人工/异步任务）后续迭代接入：审核通过后再置
    users.is_teacher = true 与 teacher_verified_at，当前不做状态变更。
    """
    log.info(
        "teacher_certification_submitted",
        user_id=str(user.id),
    )
    return {"status": "pending_review"}
