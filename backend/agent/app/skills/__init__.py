"""Skill 注册表包（§3.7）：内置 YAML + 角色过滤下发。"""

from __future__ import annotations

from app.skills.loader import (
    SkillDefinition,
    SkillError,
    load_skills,
    toolbar_for_role,
)

__all__ = ["SkillDefinition", "SkillError", "load_skills", "toolbar_for_role"]
