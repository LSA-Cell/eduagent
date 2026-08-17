"""知识库文档解析任务：上传确认后异步解析、分块、embedding 并写入 kb_chunks。

流程：MinIO 取文件确认存在（get_object）→ 多模态服务解析文本
（rag_service.call_parse_document）→ rag_service.parse_and_index_document
分块入库 → 回写 status=completed；任一环节失败 → status=failed（错误记入
metadata_.error，不再抛出，避免 Celery 自动重试放大故障）。
"""

from __future__ import annotations

from typing import Any

from eduagent_shared import db
from eduagent_shared.config import get_settings
from eduagent_shared.logging import get_logger
from eduagent_shared.models import KnowledgeDocument

from app.memory.embeddings import get_embedding_provider
from app.services import rag_service, storage_service
from app.services.llm_client import get_llm_client
from app.tasks.celery_app import celery_app, run_async

log = get_logger("agent.tasks.kb")


async def run_parse_document(
    document_id: str,
    *,
    session_factory: Any = None,
    provider: Any = None,
    storage_client: Any = None,
) -> dict[str, Any]:
    """解析知识库文档并回写状态（completed/failed），返回结果摘要。

    session_factory / provider / storage_client 可注入替身，便于离线测试。
    """
    factory = session_factory or db.get_session_factory()
    settings = get_settings()
    if provider is None:
        provider = get_embedding_provider(settings, get_llm_client(settings))
    async with factory() as session:
        document = await session.get(KnowledgeDocument, document_id)
        if document is None:
            return {"id": document_id, "status": "not_found"}
        try:
            if not document.object_key:
                raise RuntimeError("文档缺少 object_key")
            content = storage_service.download_bytes(
                document.object_key, settings=settings, client=storage_client
            )
            if not document.size_bytes:
                document.size_bytes = len(content)
            parsed_text = await rag_service.call_parse_document(
                document.object_key,
                settings.multimodal_url,
                settings.internal_service_token,
                file_type=document.file_type,
            )
            chunk_count = await rag_service.parse_and_index_document(
                session, document, parsed_text, provider
            )
            document.status = "completed"
            document.chunk_count = chunk_count
            await session.commit()
            log.info("kb_document_parsed", document_id=document_id, chunk_count=chunk_count)
            return {"id": document_id, "status": "completed", "chunk_count": chunk_count}
        except Exception as exc:  # noqa: BLE001 - 失败落库为 failed，交由用户重传
            await session.rollback()
            document = await session.get(KnowledgeDocument, document_id)
            if document is not None:
                document.status = "failed"
                document.metadata_ = {
                    **dict(document.metadata_ or {}), "error": str(exc)[:300]
                }
                await session.commit()
            log.warning("kb_document_parse_failed", document_id=document_id, error=str(exc))
            return {"id": document_id, "status": "failed", "error": str(exc)[:300]}


@celery_app.task(name="agent.kb.parse_document")
def parse_document(document_id: str) -> dict[str, Any]:
    """知识库文档解析入库（Celery 入口）。"""
    return run_async(run_parse_document(document_id))
