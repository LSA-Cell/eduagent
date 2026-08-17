"""Deps：编排层全部 I/O 的依赖注入协议 + 生产实现 DefaultDeps。

红线：Loop/工具内禁止直接 import 具体 I/O 实现（推理网关、MinIO、Redis、
DB、Celery 一律经 Deps 注入，测试用 fake 替换）；Deps 上只允许原子 I/O，
禁止新增"便利业务方法"。
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional, Protocol, Sequence

from app.events.replay import EventReplayStore
from app.services.llm_client import (  # 纯数据类型 + 错误类型复用，不含 I/O 行为
    ChatResult,
    LLMError,
    PromptTooLongError,
    ToolCall,
)
from app.tools.registry import EduToolRegistry

logger = logging.getLogger(__name__)

__all__ = [
    "Deps",
    "DefaultDeps",
    "GatewayEvent",
    "GatewayError",
    "MultimodalClient",
    "PromptTooLongError",
    "ToolCall",
    "ChatResult",
]

GatewayError = LLMError  # 网关调用失败基类（PromptTooLongError 是其子类）


# ---------------------------------------------------------------------------
# 协议定义
# ---------------------------------------------------------------------------
@dataclass
class GatewayEvent:
    """流式模型事件：文本增量或结束帧（携带 tool_calls 与 finish_reason）。"""

    text: str = ""
    tool_calls: Optional[list[ToolCall]] = None
    finish_reason: Optional[str] = None


class GatewayClient(Protocol):
    """推理网关客户端（Loop 只认此协议，禁止直连 vLLM/第三方 API）。"""

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
    ) -> ChatResult: ...

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> AsyncIterator[GatewayEvent]: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class StorageClient(Protocol):
    """对象存储（MinIO）：键格式 ``bucket/rest/of/key``。"""

    async def put_text(self, key: str, text: str) -> None: ...

    async def presigned_get(self, key: str, expires_s: int = 900) -> str: ...


class AuditSink(Protocol):
    """审计写入出口（所有实现必须 fail-soft）。"""

    async def write_run(self, **fields: Any) -> None: ...

    async def write_tool_call(self, **fields: Any) -> None: ...

    async def write_decision(self, **fields: Any) -> None: ...

    async def write_compaction(self, **fields: Any) -> None: ...


class MultimodalClient(Protocol):
    """多模态服务客户端（OCR/解析等，HTTP 调用，注入协议便于 fake 测试）。

    编排层红线：禁止 import OCR/ASR 实现，只能经此客户端 HTTP 调用多模态服务。
    """

    async def ocr_extract(
        self,
        *,
        image_url: Optional[str] = None,
        file_url: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> dict[str, Any]:
        """识别图片/文件中的文字与公式，返回 {"text": ..., "latex_blocks": [...]}。"""
        ...


class Deps(Protocol):
    """编排层 I/O 依赖全集（Loop/工具只面对此协议）。"""

    gateway: GatewayClient
    storage: Optional[StorageClient]  # None 时结果瘦身降级为截断
    audit: Optional[AuditSink]
    tool_registry: EduToolRegistry
    guardrails: Any  # GuardrailChain
    replay: Optional[EventReplayStore]  # None 时跳过重放持久化
    redis: Any  # 可选；None 时重放走进程内 ring buffer
    multimodal: Optional[MultimodalClient]  # None 时 ocr_extract 降级为可读错误
    pending_confirms: Any  # PendingConfirmStore（护栏 ask 确认闭环）
    task_dedup: Any  # TaskDedup（子 Agent 异步任务幂等去重）
    subagent_registry: Any  # SubAgentRegistry（子 Agent 定义注册表）

    def session_factory(self) -> Any:
        """返回 async 上下文管理器语义的 DB 会话工厂。"""
        ...

    async def memory_retrieve(
        self, *, user_id: str, query: str, top_k: int = 5, subject: Optional[str] = None
    ) -> list[Any]:
        """检索当前用户记忆（强制 user_id 过滤，禁止跨用户）。"""
        ...

    async def rag_search(
        self, *, query: str, subject: Optional[str] = None, top_k: int = 5
    ) -> list[Any]: ...

    async def moderate(self, text: str) -> bool:
        """内容审核（True=安全）；实现方异常时护栏链 fail-closed。"""
        ...

    def celery_send(self, task_name: str, payload: Optional[dict[str, Any]] = None) -> None:
        """投递 Celery 任务（不阻塞响应）。"""
        ...


# ---------------------------------------------------------------------------
# 生产实现（薄封装现有 LLMClient / memory_manager / rag_service /
# storage_service / celery_app；Redis 为 None 时降级内存重放）
# ---------------------------------------------------------------------------
class _GatewayAdapter:
    """LLMClient → GatewayClient 协议适配。"""

    def __init__(self, llm: Any) -> None:
        self._llm = llm

    async def chat(self, messages, *, model=None, tools=None, max_tokens=None) -> ChatResult:
        return await self._llm.chat(messages, model=model, tools=tools, max_tokens=max_tokens)

    async def chat_stream(self, messages, *, model=None, tools=None) -> AsyncIterator[GatewayEvent]:
        async for event in self._llm.chat_stream_events(messages, model=model, tools=tools):
            if "text" in event:
                yield GatewayEvent(text=event["text"])
            else:
                yield GatewayEvent(
                    tool_calls=event.get("tool_calls") or [],
                    finish_reason=event.get("finish_reason"),
                )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._llm.embed(texts)


class _MinioStorageAdapter:
    """storage_service → StorageClient 协议适配（minio SDK 为同步，丢线程池）。"""

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    @staticmethod
    def _split(key: str) -> tuple[str, str]:
        bucket, _, object_key = key.partition("/")
        if not object_key:
            raise ValueError(f"存储键须含 bucket 前缀: {key!r}")
        return bucket, object_key

    async def put_text(self, key: str, text: str) -> None:
        from datetime import timedelta  # noqa: F401 - 保持局部 import 一致性
        from app.services import storage_service

        bucket, object_key = self._split(key)
        data = text.encode("utf-8")

        def _put() -> None:
            client = storage_service.get_minio_client(self._settings)
            storage_service.ensure_bucket(client, bucket)
            client.put_object(
                bucket, object_key, io.BytesIO(data), len(data), content_type="text/plain"
            )

        await asyncio.to_thread(_put)

    async def presigned_get(self, key: str, expires_s: int = 900) -> str:
        from datetime import timedelta

        from app.services import storage_service

        bucket, object_key = self._split(key)

        def _url() -> str:
            client = storage_service.get_minio_client(self._settings)
            storage_service.ensure_bucket(client, bucket)
            return client.presigned_get_object(
                bucket, object_key, expires=timedelta(seconds=expires_s)
            )

        return await asyncio.to_thread(_url)


class DBAuditSink:
    """经 session_factory 落审计表（每次写入独立会话，fail-soft）。"""

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self._factory = session_factory

    async def _write(self, writer: Callable[..., Any], fields: dict[str, Any]) -> None:
        try:
            async with self._factory() as session:
                await writer(session, **fields)
        except Exception:  # noqa: BLE001 - 审计失败仅记日志
            logger.warning("审计写入失败（%s）", getattr(writer, "__name__", writer), exc_info=True)

    async def write_run(self, **fields: Any) -> None:
        from app.audit.runs import write_agent_run

        await self._write(write_agent_run, fields)

    async def write_tool_call(self, **fields: Any) -> None:
        from app.audit.tool_calls import write_tool_call_audit

        await self._write(write_tool_call_audit, fields)

    async def write_decision(self, **fields: Any) -> None:
        from app.audit.decisions import write_guardrail_decision

        await self._write(write_guardrail_decision, fields)

    async def write_compaction(self, **fields: Any) -> None:
        from app.audit.runs import write_compaction_audit

        await self._write(write_compaction_audit, fields)


_MODERATION_SYSTEM = (
    "你是教育场景内容审核员。判断用户内容是否含不当内容"
    "（色情/暴力/自伤/违法犯罪诱导等）。注意：生物、医学、健康课程的"
    "正常专业术语不算不当内容，不得误伤。只回答 safe 或 unsafe。"
)


class _MultimodalAdapter:
    """多模态服务 HTTP 客户端（契约对齐 multimodal 服务的 POST /ocr）。"""

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    async def ocr_extract(
        self,
        *,
        image_url: Optional[str] = None,
        file_url: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> dict[str, Any]:
        import httpx

        url = f"{self._settings.multimodal_url.rstrip('/')}/ocr"
        form: dict[str, Any] = {}
        if image_url:
            form["image_url"] = image_url
        headers = {"X-Internal-Token": self._settings.internal_service_token}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, data=form, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
        return {
            "text": payload.get("text", ""),
            "latex_blocks": payload.get("latex_blocks") or [],
        }


class DefaultDeps:
    """生产 Deps：薄封装现有服务；redis=None 时重放降级进程内 ring buffer。"""

    def __init__(
        self,
        settings: Any = None,
        *,
        llm: Any = None,
        tool_registry: Optional[EduToolRegistry] = None,
        session_factory: Optional[Callable[[], Any]] = None,
        redis: Any = None,
        storage: Any = None,
        audit: Any = None,
        guardrails: Any = None,
        replay: Optional[EventReplayStore] = None,
        multimodal: Any = None,
        pending_confirms: Any = None,
        task_dedup: Any = None,
        subagent_registry: Any = None,
    ) -> None:
        if settings is None:
            from eduagent_shared.config import get_settings

            settings = get_settings()
        if llm is None:
            from app.services.llm_client import get_llm_client

            llm = get_llm_client(settings)
        if session_factory is None:
            from eduagent_shared.db import get_session_factory

            session_factory = get_session_factory()
        self._settings = settings
        self._llm = llm
        self._session_factory = session_factory
        self.gateway: GatewayClient = _GatewayAdapter(llm)
        self.storage = storage if storage is not None else _MinioStorageAdapter(settings)
        self.audit = audit if audit is not None else DBAuditSink(session_factory)
        if tool_registry is None:
            from app.tools.builtin import create_builtin_registry  # 延迟 import 避免循环

            tool_registry = create_builtin_registry()
        self.tool_registry = tool_registry
        self.redis = redis
        self.replay = replay if replay is not None else EventReplayStore(redis)
        if multimodal is None:
            multimodal = _MultimodalAdapter(settings)
        self.multimodal = multimodal
        if pending_confirms is None:
            from app.guardrails.pending import PendingConfirmStore

            pending_confirms = PendingConfirmStore()
        self.pending_confirms = pending_confirms
        if task_dedup is None:
            from app.subagents.celery_bridge import TaskDedup

            task_dedup = TaskDedup(redis)
        self.task_dedup = task_dedup
        if subagent_registry is None:
            from app.subagents.loader import load_registry

            subagent_registry = load_registry()  # 内置 YAML 基线（DB 双读预留）
        self.subagent_registry = subagent_registry
        if guardrails is None:
            from app.guardrails.chain import GuardrailChain

            guardrails = GuardrailChain(self, audit=self._audit_decision)
        self.guardrails = guardrails

    # -- 原子 I/O ------------------------------------------------------------
    def session_factory(self) -> Any:
        return self._session_factory()

    async def _audit_decision(self, fields: dict[str, Any]) -> None:
        if self.audit is not None:
            await self.audit.write_decision(**fields)

    async def memory_retrieve(
        self, *, user_id: str, query: str, top_k: int = 5, subject: Optional[str] = None
    ) -> list[Any]:
        from app.memory import memory_manager

        embedding: Optional[list[float]] = None
        try:
            embedding = (await self._llm.embed([query]))[0]
        except Exception:  # noqa: BLE001 - embedding 失败退化为无向量检索
            embedding = None
        async with self._session_factory() as session:
            return await memory_manager.retrieve_memories(
                session, user_id, query_embedding=embedding, top_k=top_k
            )

    async def rag_search(
        self, *, query: str, subject: Optional[str] = None, top_k: int = 5
    ) -> list[Any]:
        from app.services import rag_service

        async with self._session_factory() as session:
            return await rag_service.hybrid_search(session, query, subject=subject, top_k=top_k)

    async def moderate(self, text: str) -> bool:
        result = await self._llm.chat(
            [
                {"role": "system", "content": _MODERATION_SYSTEM},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
            max_tokens=8,
        )
        verdict = (result.content or "").strip().lower()
        return not (verdict.startswith("unsafe") or verdict.startswith("不安全"))

    def celery_send(self, task_name: str, payload: Optional[dict[str, Any]] = None) -> None:
        from app.tasks.celery_app import celery_app

        celery_app.send_task(task_name, kwargs=payload or {})
