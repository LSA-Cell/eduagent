"""工具系统基类与注册表。

扩展方式：新工具只需定义 JSON Schema + handler，然后
`registry.register_tool(schema, handler)`（或装饰器写法），无需改动编排代码。
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

# handler 签名：async/sync (arguments: dict, ctx: ToolContext) -> dict
ToolHandler = Callable[["dict[str, Any]", "ToolContext"], Awaitable[dict[str, Any]]]


class ToolError(RuntimeError):
    """工具注册/执行错误。"""


@dataclass
class ToolContext:
    """工具执行上下文（由聊天编排注入）。"""

    user_id: str
    session_id: Optional[str] = None
    session: Any = None  # DB 会话（鸭子类型）
    settings: Any = None
    llm: Any = None  # LLMClient
    deps: Any = None  # 编排层 Deps（新工具系统经此做全部 I/O，测试注入 fake）
    run_id: Optional[str] = None  # 当前 Loop 运行 ID（审计/存储键用）
    # Wave B 新增（子 Agent 派发/角色门槛用；默认值为顶层调用语义）
    role: Optional[str] = None  # 当前用户身份（student/teacher/parent）
    is_teacher: bool = False
    subject: Optional[str] = None
    grade_level: Optional[str] = None
    depth: int = 1  # Agent 深度：父 Loop=1，子 Agent=2（红线：最大深度 2）
    # Wave C 新增（交互协作机制；均为 Loop 注入的运行时句柄，默认 None 不影响旧调用方）
    turn_state: Any = None  # TurnState（ask 频次/todo 快照/stop-hook 计数用）
    event_sink: Any = None  # Callable[[str, dict], None]：工具推 SSE 事件（ask/plan.status/plan.outline）
    skill_code: Optional[str] = None  # 触发技能编码（plan 白名单/todo 门槛用）
    plan_mode: bool = False  # drafting/awaiting_confirm 期间为 True：执行器仅放行只读工具


@dataclass
class Tool:
    """已注册工具：JSON Schema + 处理器。"""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    handler: Optional[ToolHandler] = None

    def openai_schema(self) -> dict[str, Any]:
        """转换为 OpenAI tools 数组元素格式，供推理网关使用。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }


class ToolRegistry:
    """工具注册表：schema 校验、按名查找、安全执行。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register_tool(
        self,
        schema: dict[str, Any],
        handler: ToolHandler,
    ) -> Tool:
        """注册工具。schema 为 {"name", "description", "parameters"} 结构。"""
        name = schema.get("name")
        if not name or not isinstance(name, str):
            raise ToolError("工具 schema 缺少合法的 name")
        if not callable(handler):
            raise ToolError(f"工具 {name} 的 handler 不可调用")
        if name in self._tools:
            raise ToolError(f"工具重复注册: {name}")
        tool = Tool(
            name=name,
            description=str(schema.get("description", "")),
            parameters=schema.get("parameters") or {"type": "object", "properties": {}},
            handler=handler,
        )
        self._tools[name] = tool
        return tool

    def tool(self, schema: dict[str, Any]) -> Callable[[ToolHandler], ToolHandler]:
        """装饰器写法：@registry.tool(schema)。"""

        def decorator(handler: ToolHandler) -> ToolHandler:
            self.register_tool(schema, handler)
            return handler

        return decorator

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolError(f"未注册的工具: {name}") from None

    def names(self) -> list[str]:
        return sorted(self._tools)

    def openai_schemas(self) -> list[dict[str, Any]]:
        return [t.openai_schema() for t in self._tools.values()]

    async def execute(
        self, name: str, arguments: dict[str, Any], ctx: ToolContext
    ) -> dict[str, Any]:
        """按名执行工具；任何异常都收敛为 {"error": ...}，不打断编排循环。"""
        try:
            tool = self.get(name)
        except ToolError as exc:
            return {"error": str(exc)}
        try:
            result = tool.handler(arguments or {}, ctx)  # type: ignore[misc]
            if inspect.isawaitable(result):
                result = await result
            return result if isinstance(result, dict) else {"result": result}
        except Exception as exc:  # noqa: BLE001 - 工具异常需回填给 LLM
            return {"error": f"工具 {name} 执行失败: {exc}"}


# 进程级默认注册表；app.tools/__init__ 导入各工具模块完成内置工具注册
registry = ToolRegistry()


# ---------------------------------------------------------------------------
# 新版工具协议（EduTool）：元数据自描述 + Pydantic 入参 + 卡片返回契约。
# 与上面的旧 Tool/ToolRegistry 并存：旧工具继续用 @registry.tool 注册，
# 新工具实现 EduTool 并注册进 app.tools.registry.EduToolRegistry。
# ---------------------------------------------------------------------------
class ToolResult(BaseModel):
    """工具返回契约：给模型看的文本 + 给前端看的卡片。"""

    content: str  # 给模型看的文本（执行器负责瘦身）
    card_type: Optional[str] = None  # grading/ppt_preview/lesson_plan_preview/exam_preview/...
    card_data: Optional[dict[str, Any]] = None  # 前端卡片结构化数据
    file_urls: list[str] = Field(default_factory=list)  # MinIO 预签名 URL（过期 ≤1 小时）
    # Wave C：ask_user_question 挂起标记（Loop 见此后挂起等答复/超时；不进卡片事件）
    pending_ask: Optional[dict[str, Any]] = None


@runtime_checkable
class EduTool(Protocol):
    """新版工具协议（等价 Claude Code 的 Tool 接口）。

    红线：元数据缺一即拒绝注册；工具内禁止硬编码模型名/模型地址；
    只读工具（is_read_only=True）出现写副作用视为线上事故。
    """

    name: str  # 全局唯一，snake_case
    description: str  # 给模型看的一句话能力描述
    input_model: type[BaseModel]  # 入参 Schema
    is_read_only: bool  # 缺省按"写"处理（保守默认，强制显式声明）
    is_concurrency_safe: bool
    role_required: Optional[str]  # "teacher" 时仅教师可见/可调
    max_result_chars: int  # 超限 → 存 MinIO + 摘要替换
    timeout_s: float  # 单工具超时上限（执行器 asyncio.wait_for 强制）
    card_type: Optional[str]  # 前端卡片类型枚举
    deferred: bool  # 低频工具不进初始 prompt，靠工具检索发现
    interrupt_behavior: str  # 打断行为：cancel/block（未声明默认 block，禁止隐式 cancel）

    async def call(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        """执行工具。副作用（写库/写文件/发请求）必须在 docstring 中声明。"""
        ...


class EduToolBase:
    """EduTool 便捷基类：提供保守默认值，子类只需声明元数据 + 实现 call。"""

    name: str = ""
    description: str = ""
    input_model: type[BaseModel] = BaseModel
    is_read_only: bool = False
    is_concurrency_safe: bool = False
    role_required: Optional[str] = None
    max_result_chars: int = 20_000
    timeout_s: float = 10
    card_type: Optional[str] = None
    deferred: bool = False
    # Wave C：打断行为（增强机制 §2.3）。cancel=工具执行中收到消息真实取消
    # asyncio.Task 并推 tool.cancelled；block=排队等工具结束。未声明默认 block。
    interrupt_behavior: str = "block"

    async def call(self, args: BaseModel, ctx: ToolContext) -> ToolResult:  # pragma: no cover
        raise NotImplementedError
