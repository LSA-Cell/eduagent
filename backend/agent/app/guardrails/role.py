"""环节 ② 角色校验：role_required 与用户身份匹配（教师专属校验 is_teacher）。"""

from __future__ import annotations

from typing import Any

from app.guardrails.decision import Decision


class RoleStage:
    """工具声明 role_required 时，用户身份必须匹配；teacher 另认 is_teacher 标记。"""

    name = "role"

    async def check(self, tool: Any, config: Any, arguments: dict[str, Any]) -> Decision:
        required = getattr(tool, "role_required", None)
        if not required:
            return Decision.allow(self.name)
        role = getattr(config, "role", "student")
        if required == "teacher":
            is_teacher = role == "teacher" or bool(getattr(config, "is_teacher", False))
            if not is_teacher:
                return Decision.deny(self.name, f"工具 {tool.name} 仅教师可用")
        elif role != required:
            return Decision.deny(self.name, f"工具 {tool.name} 仅 {required} 可用")
        return Decision.allow(self.name)
