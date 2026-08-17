"""TurnConfig：入循环时快照一次，全程不可变（frozen=True）。

红线：禁止循环内任何代码修改 Config；需要变更必须终止当前 Loop 重新发起。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

DEFAULT_MAX_TURNS = 20
DEFAULT_TOKEN_BUDGET = 100_000
DEFAULT_MAX_DURATION_S = 300.0
DEFAULT_MAX_TOOL_RESULT_CHARS = 20_000
DEFAULT_MODEL_WINDOW_TOKENS = 32_000
AUTOCOMPACT_BUFFER_TOKENS = 13_000  # 窗口触发 buffer（设计文档 §3.4 固定值）


class TurnConfig(BaseModel, frozen=True):
    """一次 Loop 运行的不可变配置快照。"""

    user_id: str
    session_id: Optional[str] = None
    subject: Optional[str] = None
    grade_level: Optional[str] = None
    role: Literal["student", "teacher", "parent"] = "student"
    is_teacher: bool = False  # 教师身份标记（role 校验用，双保险）
    is_minor: bool = False  # 未成年人（家长模式环节用）
    quota_tier: str = "free"  # 身份档：free/paid/teacher
    model_route: Optional[str] = None  # 推理网关模型路由（禁止工具硬编码模型名）
    confirm_token: Optional[str] = None  # 护栏 ask 确认令牌（客户端重发请求时携带）
    depth: int = 1  # Agent 深度：父 Loop=1，子 Agent=2（红线：最大深度 2）

    max_turns: int = DEFAULT_MAX_TURNS
    token_budget: int = DEFAULT_TOKEN_BUDGET
    max_duration_s: float = DEFAULT_MAX_DURATION_S
    max_tool_result_chars: int = DEFAULT_MAX_TOOL_RESULT_CHARS

    # 压缩管线参数
    model_window_tokens: int = DEFAULT_MODEL_WINDOW_TOKENS
    compact_buffer_tokens: int = AUTOCOMPACT_BUFFER_TOKENS
    microcompact_keep: int = 3
    slim_max_chars: int = 2_000

    # 记忆预取参数
    prefetch_timeout_ms: int = 300
    memory_block_max_tokens: int = 800

    @classmethod
    def from_request(cls, req: Any, **overrides: Any) -> "TurnConfig":
        """从聊天请求（ChatRequest 鸭子类型）构建快照。

        req 上读 user_id/session_id/subject/model；role/grade_level/is_minor
        及 max_turns/token_budget 等调优参数优先取 overrides，其次 req 同名
        属性，最后默认值。
        """
        def pick(name: str, default: Any = None) -> Any:
            if name in overrides:
                return overrides[name]
            value = getattr(req, name, None)
            return value if value is not None else default

        tunable = {
            key: pick(key)
            for key in (
                "max_turns", "token_budget", "max_duration_s",
                "max_tool_result_chars", "model_window_tokens",
                "prefetch_timeout_ms", "memory_block_max_tokens",
            )
            if pick(key) is not None
        }
        return cls(
            user_id=str(req.user_id),
            session_id=getattr(req, "session_id", None),
            subject=getattr(req, "subject", None),
            grade_level=pick("grade_level"),
            role=pick("role", "student"),
            is_teacher=bool(pick("is_teacher", False)),
            is_minor=bool(pick("is_minor", False)),
            quota_tier=pick("quota_tier", "free"),
            model_route=getattr(req, "model", None),
            confirm_token=pick("confirm_token"),
            depth=max(1, int(pick("depth", 1) or 1)),
            **tunable,
        )
