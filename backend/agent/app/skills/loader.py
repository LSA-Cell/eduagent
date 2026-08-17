"""Skill 注册表加载：内置 YAML 基线 + 校验 + 按角色过滤下发 + DB 双读预留。

红线（设计文档 §3.7）：
- 提示词模板注册表化（builtin_skills.yaml / skill_definitions 表），禁止硬编码进 .py；
- 身份过滤在服务端完成（toolbar_for_role），前端过滤仅作 UX；
- 新增 Skill 只需插表 / 加 YAML，Loop 代码零改动。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

BUILTIN_SKILLS_PATH = Path(__file__).parent / "builtin_skills.yaml"

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
VALID_ROLES = frozenset({"student", "teacher", "parent"})


class SkillError(RuntimeError):
    """Skill 注册表加载/校验失败。"""


class SkillDefinition(BaseModel):
    """一条 Skill 记录（工具栏工具的结构化入口定义）。"""

    code: str
    name: str = Field(min_length=1)
    icon: str = ""
    roles: list[str] = Field(min_length=1)
    prompt_template: str = Field(min_length=1)
    input_form_schema: list[dict[str, Any]] = Field(default_factory=list)
    card_type: Optional[str] = None
    sort_order: int = 0
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def _code_snake(cls, value: str) -> str:
        if not _CODE_RE.match(value or ""):
            raise ValueError(f"Skill code 需 snake_case: {value!r}")
        return value

    @field_validator("roles")
    @classmethod
    def _roles_valid(cls, value: list[str]) -> list[str]:
        invalid = [r for r in value if r not in VALID_ROLES]
        if invalid:
            raise ValueError(f"非法角色: {invalid}（合法：{sorted(VALID_ROLES)}）")
        return value


def _parse_skill(raw: Any, *, source: str) -> SkillDefinition:
    if not isinstance(raw, dict):
        raise SkillError(f"{source}: Skill 必须是映射，得到 {type(raw).__name__}")
    try:
        return SkillDefinition.model_validate(raw)
    except ValidationError as exc:
        raise SkillError(f"{source}: Skill 校验失败: {exc}") from exc


def load_skills(path: Optional[Path] = None) -> list[SkillDefinition]:
    """加载内置 YAML（任一非法即整体拒绝，启动期暴露）；code 重复报错。"""
    file = path or BUILTIN_SKILLS_PATH
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SkillError(f"{file.name}: YAML 解析失败: {exc}") from exc
    if not isinstance(raw, list):
        raise SkillError(f"{file.name}: 顶层必须是 Skill 列表")
    skills: list[SkillDefinition] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        skill = _parse_skill(item, source=f"{file.name}#{i}")
        if skill.code in seen:
            raise SkillError(f"Skill code 重复: {skill.code}")
        seen.add(skill.code)
        skills.append(skill)
    return skills


def merge_db_rows(
    base: list[SkillDefinition], rows: list[dict[str, Any]]
) -> list[SkillDefinition]:
    """DB 行覆盖/新增 YAML 基线；is_active=false 的行下架同名 Skill。"""
    merged = {s.code: s for s in base}
    for row in rows:
        skill = _parse_skill(row, source="skill_definitions")
        if skill.is_active:
            merged[skill.code] = skill
        else:
            merged.pop(skill.code, None)
    return sorted(merged.values(), key=lambda s: (s.sort_order, s.code))


async def _fetch_db_rows(session_factory: Callable[[], Any]) -> list[dict[str, Any]]:
    """读 skill_definitions 表（文本 SQL；表不存在时抛错由调用方兜底）。"""
    from sqlalchemy import text

    async with session_factory() as session:
        result = await session.execute(
            text(
                "SELECT code, name, icon, roles, prompt_template, input_form_schema, "
                "card_type, sort_order, is_active FROM skill_definitions"
            )
        )
        rows = result.all() if hasattr(result, "all") else []
    return [dict(row._mapping) if hasattr(row, "_mapping") else dict(row) for row in rows]


async def load_skills_async(
    *,
    path: Optional[Path] = None,
    session_factory: Optional[Callable[[], Any]] = None,
) -> list[SkillDefinition]:
    """DB 双读预留入口：提供 session_factory 时合并 skill_definitions 行。

    读表失败 fail-soft 回退纯 YAML 基线，仅记日志。
    """
    skills = load_skills(path)
    if session_factory is not None:
        try:
            skills = merge_db_rows(skills, await _fetch_db_rows(session_factory))
        except Exception:  # noqa: BLE001
            logger.warning("skill_definitions 读表失败，回退 YAML 基线", exc_info=True)
    return skills


def toolbar_for_role(skills: list[SkillDefinition], role: str) -> list[dict[str, Any]]:
    """服务端身份过滤 + sort_order 排序，返回下发契约（dict 列表）。

    只下发 is_active 且 roles 含当前身份的 Skill；非法 role 抛 SkillError
    （路由层转 422/400）。
    """
    if role not in VALID_ROLES:
        raise SkillError(f"非法角色: {role!r}（合法：{sorted(VALID_ROLES)}）")
    visible = [s for s in skills if s.is_active and role in s.roles]
    visible.sort(key=lambda s: (s.sort_order, s.code))
    return [s.model_dump() for s in visible]
