"""记忆并发预取：asyncio.create_task 启动，与首 Token 请求并发。

红线：预取禁止阻塞首 Token —— Loop 在组装 System Prompt 前才 await，
且 300ms 超时降级为空注入。检索内容：显式记忆 + 到期复习项占位
（现有 memory_manager 能力内实现，弱知识点/复习项可无则略）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_PREFETCH_TIMEOUT_MS = 300
DEFAULT_TOP_K = 5


async def _collect(
    deps: Any,
    user_id: str,
    query: str,
    *,
    top_k: int,
    subject: Optional[str],
) -> list[Any]:
    """实际检索协程：显式记忆经 deps.memory_retrieve；复习项为占位（可无则略）。"""
    items = await deps.memory_retrieve(
        user_id=user_id, query=query, top_k=top_k, subject=subject
    )
    return list(items or [])


def prefetch_memories(
    deps: Any,
    user_id: str,
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    subject: Optional[str] = None,
) -> "asyncio.Task[list[Any]]":
    """启动并发预取任务（调用方持有返回的 Task，稍后再 await）。"""
    return asyncio.create_task(
        _collect(deps, user_id, query, top_k=top_k, subject=subject)
    )


async def await_with_timeout(
    task: "asyncio.Task[list[Any]]", timeout_ms: int = DEFAULT_PREFETCH_TIMEOUT_MS
) -> list[Any]:
    """带超时等待预取结果；超时/异常一律降级为空注入。

    底层任务用 shield 保护：超时只放弃等待，不取消检索协程本身
    （避免半途中断 DB 会话）。
    """
    try:
        return list(await asyncio.wait_for(asyncio.shield(task), timeout_ms / 1000))
    except asyncio.TimeoutError:
        logger.info("记忆预取超时（>%dms），降级为空注入", timeout_ms)
        return []
    except Exception:  # noqa: BLE001 - 检索失败不阻塞主会话
        logger.warning("记忆预取失败，降级为空注入", exc_info=True)
        return []
