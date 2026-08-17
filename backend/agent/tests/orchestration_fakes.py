"""编排层测试公共 fake：脚本化网关流 / 假存储 / 假审计 / 假工具 / FakeDeps。

全部离线：不触网、不触 DB、不触 Redis。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable, Optional, Sequence

from pydantic import BaseModel

from app.events.replay import EventReplayStore
from app.guardrails.chain import GuardrailChain
from app.loop.deps_protocol import GatewayEvent
from app.services.llm_client import ChatResult, ToolCall
from app.tools.base import EduToolBase, ToolResult
from app.tools.registry import EduToolRegistry


class ScriptedGateway:
    """脚本化流式网关替身。

    - chat_stream：依次弹出 stream_scripts（元素为 GatewayEvent 列表，
      或异常实例则抛出）；用完后返回 default_script（可循环）；
    - chat：依次弹出 chat_results（压缩摘要用），默认返回 default_summary；
    - embed：返回固定伪向量。
    """

    def __init__(
        self,
        *,
        stream_scripts: Optional[Sequence[Any]] = None,
        default_script: Optional[Sequence[Any]] = None,
        chat_results: Optional[Sequence[Any]] = None,
        default_summary: str = "（摘要）",
    ) -> None:
        self.stream_scripts = [list(s) if not isinstance(s, Exception) else s for s in (stream_scripts or [])]
        self.default_script = list(default_script) if default_script else [GatewayEvent(finish_reason="stop")]
        self.chat_results = list(chat_results or [])
        self.default_summary = default_summary
        self.stream_calls: list[dict[str, Any]] = []
        self.chat_calls: list[dict[str, Any]] = []

    async def chat(self, messages, *, model=None, tools=None, max_tokens=None) -> ChatResult:
        self.chat_calls.append({"messages": messages, "tools": tools, "model": model})
        if self.chat_results:
            result = self.chat_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return ChatResult(content=self.default_summary, finish_reason="stop")

    async def chat_stream(self, messages, *, model=None, tools=None) -> AsyncIterator[GatewayEvent]:
        self.stream_calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        script = self.stream_scripts.pop(0) if self.stream_scripts else self.default_script
        if isinstance(script, Exception):
            raise script
        for event in script:
            yield event

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)) for t in texts]]


class FakeStorage:
    """MinIO 替身：记录 put_text，presigned_get 返回假 URL。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.objects: dict[str, str] = {}
        self.fail = fail

    async def put_text(self, key: str, text: str) -> None:
        if self.fail:
            raise RuntimeError("minio 不可用")
        self.objects[key] = text

    async def presigned_get(self, key: str, expires_s: int = 900) -> str:
        if self.fail:
            raise RuntimeError("minio 不可用")
        return f"https://minio.test/{key}?expires={expires_s}"


class FakeAuditSink:
    """审计替身：分类记录全部写入。"""

    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.compactions: list[dict[str, Any]] = []

    async def write_run(self, **fields: Any) -> None:
        self.runs.append(fields)

    async def write_tool_call(self, **fields: Any) -> None:
        self.tool_calls.append(fields)

    async def write_decision(self, **fields: Any) -> None:
        self.decisions.append(fields)

    async def write_compaction(self, **fields: Any) -> None:
        self.compactions.append(fields)


class FakeDeps:
    """Deps 协议 fake：属性直挂，I/O 全脚本化。"""

    def __init__(
        self,
        *,
        gateway: Optional[ScriptedGateway] = None,
        registry: Optional[EduToolRegistry] = None,
        session: Any = None,
        storage: Any = None,
        audit: Optional[FakeAuditSink] = None,
        memories: Any = None,  # list 或异常 或 (delay_s, list)
        moderate_result: Any = True,  # bool 或异常
        replay: Any = "memory",
        multimodal: Any = None,  # MultimodalClient fake（None=未配置）
        subagent_registry: Any = None,  # None 时 dispatch 兜底加载内置 YAML
    ) -> None:
        from app.guardrails.pending import PendingConfirmStore
        from app.subagents.celery_bridge import TaskDedup

        self.gateway = gateway or ScriptedGateway()
        self.tool_registry = registry or EduToolRegistry()
        self.storage = storage if storage is not None else FakeStorage()
        self.audit = audit if audit is not None else FakeAuditSink()
        self.redis = None
        self.replay = EventReplayStore() if replay == "memory" else replay
        self._session = session
        self._memories = memories if memories is not None else []
        self._moderate_result = moderate_result
        self.celery_sent: list[tuple[str, dict[str, Any]]] = []
        self.multimodal = multimodal
        self.pending_confirms = PendingConfirmStore()
        self.task_dedup = TaskDedup()
        self.subagent_registry = subagent_registry
        self.guardrails = GuardrailChain(
            self, audit=lambda fields: self.audit.write_decision(**fields)
        )

    def session_factory(self) -> Any:
        if self._session is None:
            raise RuntimeError("FakeDeps 未注入 session")
        return self._session

    async def memory_retrieve(
        self, *, user_id: str, query: str, top_k: int = 5, subject: Optional[str] = None
    ) -> list[Any]:
        memories = self._memories
        if isinstance(memories, Exception):
            raise memories
        if isinstance(memories, tuple):
            delay, items = memories
            await asyncio.sleep(delay)
            return list(items)
        return list(memories)

    async def rag_search(self, *, query: str, subject: Optional[str] = None, top_k: int = 5) -> list[Any]:
        return []

    async def moderate(self, text: str) -> bool:
        if isinstance(self._moderate_result, Exception):
            raise self._moderate_result
        return bool(self._moderate_result)

    def celery_send(self, task_name: str, payload: Optional[dict[str, Any]] = None) -> None:
        self.celery_sent.append((task_name, payload or {}))


# ---------------------------------------------------------------------------
# 假工具
# ---------------------------------------------------------------------------
class _EchoArgs(BaseModel):
    text: str = ""


def make_tool(
    name: str = "echo_tool",
    *,
    description: str = "回声工具",
    result: Any = None,  # str / ToolResult / Exception
    delay_s: float = 0.0,
    is_read_only: bool = True,
    is_concurrency_safe: bool = True,
    role_required: Optional[str] = None,
    max_result_chars: int = 20_000,
    timeout_s: float = 10,
    card_type: Optional[str] = None,
    deferred: bool = False,
    input_model: Any = _EchoArgs,
    calls: Optional[list] = None,
) -> EduToolBase:
    """构造一个脚本化 EduTool（calls 列表传入可记录调用次数）。"""

    class _FakeTool(EduToolBase):
        pass

    tool = _FakeTool()
    tool.name = name  # type: ignore[misc]
    tool.description = description  # type: ignore[misc]
    tool.input_model = input_model  # type: ignore[misc]
    tool.is_read_only = is_read_only  # type: ignore[misc]
    tool.is_concurrency_safe = is_concurrency_safe  # type: ignore[misc]
    tool.role_required = role_required  # type: ignore[misc]
    tool.max_result_chars = max_result_chars  # type: ignore[misc]
    tool.timeout_s = timeout_s  # type: ignore[misc]
    tool.card_type = card_type  # type: ignore[misc]
    tool.deferred = deferred  # type: ignore[misc]

    async def call(args: BaseModel, ctx: Any) -> ToolResult:
        if calls is not None:
            calls.append({"name": name, "args": args.model_dump(), "user_id": ctx.user_id})
        if delay_s:
            await asyncio.sleep(delay_s)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, ToolResult):
            return result
        return ToolResult(content=str(result) if result is not None else f"echo:{args.text}",
                          card_type=card_type,
                          card_data={"echo": args.text} if card_type else None)

    tool.call = call  # type: ignore[misc]
    return tool


def make_registry(*tools: Any) -> EduToolRegistry:
    registry = EduToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def tool_call(name: str, arguments: Optional[dict] = None, call_id: str = "") -> ToolCall:
    return ToolCall(id=call_id or f"call_{name}", name=name, arguments=arguments or {})
