"""知识库上传链路测试：预签名 URL / 确认派发 / 列表 / 详情 / 删除（全部离线）。

MinIO 客户端与多模态解析网络调用均打桩；Celery eager 模式验证任务端到端。
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from eduagent_shared import db
from eduagent_shared.models import KbChunk, KnowledgeDocument

from app import deps
from app.main import create_app
from app.memory.embeddings import HashEmbeddingProvider
from app.services import rag_service, storage_service
from app.tasks import kb_tasks
from tests.conftest import TEST_USER_ID, FakeSession, as_factory, auth_headers

OTHER_USER_ID = str(uuid.uuid4())
PARSED_TEXT = "第一章 二次函数的基本性质与图像。" * 60  # ~900 字，通用分块可得 2 块


def _make_document(
    user_id: str = TEST_USER_ID,
    *,
    status: str = "uploading",
    object_key: str | None = "kb/u/abc.pdf",
    file_type: str = "pdf",
) -> KnowledgeDocument:
    return KnowledgeDocument(
        user_id=user_id,
        title="数学讲义",
        file_name="数学讲义.pdf",
        file_type=file_type,
        object_key=object_key,
        status=status,
    )


@pytest.fixture
def app_and_session(fake_session: FakeSession, monkeypatch):
    """装配测试应用：覆盖 DB 依赖，打桩 MinIO 预签名与 Celery 任务派发。"""
    app = create_app()

    async def _fake_db():
        yield fake_session

    app.dependency_overrides[deps.get_db] = _fake_db

    presigned_calls: list[dict[str, Any]] = []

    def _fake_presigned_put(object_key: str, **kwargs: Any) -> str:
        presigned_calls.append({"object_key": object_key, **kwargs})
        expires = kwargs.get("expires", 0)
        return f"http://minio:9000/eduagent-uploads/{object_key}?X-Amz-Expires={expires}"

    removed: list[str] = []
    monkeypatch.setattr(storage_service, "create_presigned_put", _fake_presigned_put)
    monkeypatch.setattr(
        storage_service, "remove_object",
        lambda object_key, **kwargs: removed.append(object_key),
    )

    dispatched: list[tuple[str, tuple[Any, ...]]] = []

    def _fake_delay(*args: Any, **kwargs: Any):
        dispatched.append(("agent.kb.parse_document", args))
        return SimpleNamespace(id="task-kb-test")

    monkeypatch.setattr(kb_tasks.parse_document, "delay", _fake_delay)
    return app, fake_session, presigned_calls, removed, dispatched


async def _request(
    app, method: str, path: str, *, json: Any = None, headers: dict[str, str] | None = None
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, json=json, headers=headers or auth_headers())


class TestUploadUrl:
    async def test_upload_url_success(self, app_and_session) -> None:
        app, session, presigned_calls, _, _ = app_and_session

        resp = await _request(
            app, "POST", "/kb/documents/upload-url",
            json={"filename": "数学讲义.pdf", "content_type": "application/pdf", "size": 2048},
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["expires_in"] <= 3600
        assert body["object_key"].startswith(f"kb/{TEST_USER_ID}/")
        assert body["object_key"].endswith(".pdf")
        # 预签名 URL 含 object_key，且实际使用的 expires 不超过 3600 秒
        assert body["object_key"] in body["upload_url"]
        assert presigned_calls[0]["expires"] <= 3600
        # 预创建文档行：status=uploading
        documents = [o for o in session.added if isinstance(o, KnowledgeDocument)]
        assert len(documents) == 1
        assert documents[0].status == "uploading"
        assert documents[0].file_type == "pdf"
        assert str(documents[0].id) == body["document_id"]

    async def test_upload_url_rejects_bad_extension(self, app_and_session) -> None:
        app, _, _, _, _ = app_and_session

        resp_exe = await _request(
            app, "POST", "/kb/documents/upload-url", json={"filename": "evil.exe"}
        )
        resp_noext = await _request(
            app, "POST", "/kb/documents/upload-url", json={"filename": "noext"}
        )

        assert resp_exe.status_code == 400
        assert resp_noext.status_code == 400

    async def test_upload_url_rejects_oversize(self, app_and_session) -> None:
        app, _, _, _, _ = app_and_session

        resp = await _request(
            app, "POST", "/kb/documents/upload-url",
            json={"filename": "big.pdf", "size": 50 * 1024 * 1024 + 1},
        )

        assert resp.status_code == 400

    async def test_upload_url_requires_internal_token(self, app_and_session) -> None:
        app, _, _, _, _ = app_and_session

        resp = await _request(
            app, "POST", "/kb/documents/upload-url",
            json={"filename": "a.pdf"}, headers={"X-User-Id": TEST_USER_ID},
        )

        assert resp.status_code == 401


class TestConfirm:
    async def test_confirm_dispatches_parse_task(self, app_and_session) -> None:
        app, session, _, _, dispatched = app_and_session
        document = _make_document()
        session.add(document)
        await session.flush()

        resp = await _request(app, "POST", f"/kb/documents/{document.id}/confirm")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "processing"
        assert body["task_id"] == "task-kb-test"
        assert document.status == "processing"
        assert dispatched == [("agent.kb.parse_document", (str(document.id),))]

    async def test_confirm_not_found_or_wrong_status(self, app_and_session) -> None:
        app, session, _, _, _ = app_and_session

        resp_missing = await _request(app, "POST", f"/kb/documents/{uuid.uuid4()}/confirm")
        assert resp_missing.status_code == 404

        document = _make_document(status="completed")  # 非 uploading 状态不可重复确认
        session.add(document)
        await session.flush()
        resp_conflict = await _request(app, "POST", f"/kb/documents/{document.id}/confirm")
        assert resp_conflict.status_code == 409


class TestListGetDelete:
    async def test_list_and_get_document(self, app_and_session) -> None:
        app, session, _, _, _ = app_and_session
        document = _make_document(status="completed")
        session.add(document)
        await session.flush()

        resp_list = await _request(app, "GET", "/kb/documents")
        assert resp_list.status_code == 200
        items = resp_list.json()["items"]
        assert [i["id"] for i in items] == [str(document.id)]
        assert items[0]["status"] == "completed"

        resp_get = await _request(app, "GET", f"/kb/documents/{document.id}")
        assert resp_get.status_code == 200
        assert resp_get.json()["object_key"] == "kb/u/abc.pdf"

    async def test_get_other_users_document_404(self, app_and_session) -> None:
        app, session, _, _, _ = app_and_session
        document = _make_document(user_id=OTHER_USER_ID)
        session.add(document)
        await session.flush()

        resp = await _request(app, "GET", f"/kb/documents/{document.id}")

        assert resp.status_code == 404

    async def test_delete_removes_chunks_and_object(self, app_and_session) -> None:
        app, session, _, removed, _ = app_and_session
        document = _make_document(status="completed")
        session.add(document)
        await session.flush()
        for index in range(2):
            session.add(KbChunk(
                document_id=document.id, user_id=document.user_id,
                chunk_index=index, content=f"块{index}",
            ))
        await session.flush()

        resp = await _request(app, "DELETE", f"/kb/documents/{document.id}")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": True, "document_id": str(document.id)}
        # 文档与 2 个 chunk 均被删除，MinIO 对象同步清理
        assert document in session.deleted
        assert len([o for o in session.deleted if isinstance(o, KbChunk)]) == 2
        assert removed == ["kb/u/abc.pdf"]

        resp_again = await _request(app, "DELETE", f"/kb/documents/{document.id}")
        assert resp_again.status_code == 404


class TestParseTask:
    async def test_run_parse_document_success(
        self, fake_session: FakeSession, monkeypatch
    ) -> None:
        """任务体：MinIO 下载 + mock 多模态解析 → 真实分块/embedding 入库。"""
        document = _make_document(status="processing")
        fake_session.add(document)
        await fake_session.flush()
        monkeypatch.setattr(
            storage_service, "download_bytes", lambda *a, **k: b"raw-file-bytes"
        )

        async def _fake_parse(*args: Any, **kwargs: Any) -> str:
            return PARSED_TEXT

        monkeypatch.setattr(rag_service, "call_parse_document", _fake_parse)

        result = await kb_tasks.run_parse_document(
            str(document.id),
            session_factory=as_factory(fake_session),
            provider=HashEmbeddingProvider(),
        )

        assert result["status"] == "completed"
        chunks = [o for o in fake_session.added if isinstance(o, KbChunk)]
        assert result["chunk_count"] == len(chunks) > 0
        assert document.status == "completed"
        assert document.chunk_count == len(chunks)
        assert chunks[0].embedding is not None

    async def test_run_parse_document_failure_marks_failed(
        self, fake_session: FakeSession, monkeypatch
    ) -> None:
        """解析异常 → status=failed 且错误写入 metadata，不向外抛出。"""
        document = _make_document(status="processing")
        fake_session.add(document)
        await fake_session.flush()
        monkeypatch.setattr(
            storage_service, "download_bytes", lambda *a, **k: b"raw-file-bytes"
        )

        async def _fake_parse(*args: Any, **kwargs: Any) -> str:
            raise RuntimeError("多模态服务不可用")

        monkeypatch.setattr(rag_service, "call_parse_document", _fake_parse)

        result = await kb_tasks.run_parse_document(
            str(document.id),
            session_factory=as_factory(fake_session),
            provider=HashEmbeddingProvider(),
        )

        assert result["status"] == "failed"
        assert "多模态服务不可用" in result["error"]
        assert document.status == "failed"
        assert "多模态服务不可用" in document.metadata_["error"]
        assert not [o for o in fake_session.added if isinstance(o, KbChunk)]

    async def test_run_parse_document_not_found(self, fake_session: FakeSession) -> None:
        result = await kb_tasks.run_parse_document(
            str(uuid.uuid4()), session_factory=as_factory(fake_session)
        )
        assert result["status"] == "not_found"


def test_parse_document_celery_eager(fake_session: FakeSession, monkeypatch) -> None:
    """Celery eager 端到端：.delay 同步执行任务，rag_service 网络调用打桩。"""
    document = _make_document(status="processing")
    fake_session.add(document)
    asyncio.run(fake_session.flush())
    monkeypatch.setattr(db, "get_session_factory", lambda: as_factory(fake_session))
    monkeypatch.setattr(storage_service, "download_bytes", lambda *a, **k: b"raw")
    monkeypatch.setattr(
        kb_tasks, "get_embedding_provider", lambda *a, **k: HashEmbeddingProvider()
    )

    async def _fake_parse(*args: Any, **kwargs: Any) -> str:
        return PARSED_TEXT

    monkeypatch.setattr(rag_service, "call_parse_document", _fake_parse)

    eager_result = kb_tasks.parse_document.delay(str(document.id))

    assert eager_result.state == "SUCCESS"
    assert eager_result.result["status"] == "completed"
    assert document.status == "completed"


def test_parse_document_task_registered() -> None:
    """任务经 celery_app include / worker 导入完成注册。"""
    from app.tasks import worker  # noqa: F401 - 导入即注册全部任务
    from app.tasks.celery_app import celery_app

    assert "agent.kb.parse_document" in celery_app.tasks
