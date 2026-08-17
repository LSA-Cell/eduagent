"""任务状态流转测试：异步渲染接口 + GET /tasks/{id}（MinIO 用 FakeStorage mock）。

TestClient 会同步等待 BackgroundTasks 执行完毕，因此请求返回后任务应已完成。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import jobs
from app.tasks import reset_task_store_for_tests
from tests.conftest import TEST_HEADERS
from tests.helpers import FakeStorage


@pytest.fixture()
def fake_storage(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(jobs, "get_storage", lambda: fake)
    reset_task_store_for_tests()
    yield fake
    reset_task_store_for_tests()


client = TestClient(app, headers=TEST_HEADERS)

_PPT_PAYLOAD = {
    "title": "分数的加法",
    "style": "minimal",
    "slides": [
        {"slide_number": 1, "layout": "title", "title": "分数的加法", "content_points": []},
        {"slide_number": 2, "layout": "content", "title": "要点", "content_points": ["分母不变"]},
    ],
}


def test_ppt_task_flow(fake_storage) -> None:
    resp = client.post("/render/ppt", json=_PPT_PAYLOAD)
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["task_id"]

    status_resp = client.get(f"/tasks/{task_id}")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["status"] == "completed"
    result = body["result"]
    assert result["slide_count"] == 2
    assert result["file_url"].startswith("http://minio.test/")
    assert result["object_key"].endswith(".pptx")
    # 文件确实“上传”到了 mock 存储
    assert result["object_key"] in fake_storage.uploads
    assert fake_storage.uploads[result["object_key"]][:2] == b"PK"  # pptx 是 zip


def test_lesson_plan_task_flow(fake_storage) -> None:
    payload = {
        "title": "分数的加法 教案",
        "course_name": "数学",
        "grade": "三年级",
        "subject": "数学",
        "duration": "45分钟",
        "template_type": "new_standard",
        "content": {
            "objectives": ["理解算理"],
            "key_points": ["法则"],
            "difficulties": [],
            "procedure": [
                {"stage": "导入", "duration": "5分钟", "teacher_activity": "提问", "student_activity": "回答", "design_intent": "激趣"}
            ],
            "board_design": "板书",
            "homework": "练习",
            "reflection": "",
        },
    }
    resp = client.post("/render/lesson_plan", json=payload)
    assert resp.status_code == 202, resp.text
    body = client.get(f"/tasks/{resp.json()['task_id']}").json()
    assert body["status"] == "completed"
    assert body["result"]["file_url"].startswith("http://minio.test/")
    assert body["result"]["object_key"].endswith(".docx")


def test_exam_paper_task_flow_two_files(fake_storage) -> None:
    payload = {
        "title": "期中测试",
        "grade": "三年级",
        "subject": "数学",
        "duration_minutes": 90,
        "total_score": 100,
        "questions": [
            {"number": 1, "type": "choice", "question": "3+5=?", "options": ["6", "8"], "answer": "B", "analysis": "", "knowledge_tag": "加法", "score": 5},
            {"number": 2, "type": "essay", "question": "应用题", "options": [], "answer": "9", "analysis": "12-3", "knowledge_tag": "减法", "score": 10},
        ],
    }
    resp = client.post("/render/exam_paper", json=payload)
    assert resp.status_code == 202, resp.text
    body = client.get(f"/tasks/{resp.json()['task_id']}").json()
    assert body["status"] == "completed"
    result = body["result"]
    assert result["student_file_url"] != result["teacher_file_url"]
    assert result["student_object_key"] in fake_storage.uploads
    assert result["teacher_object_key"] in fake_storage.uploads
    assert len(fake_storage.uploads) == 2


def test_report_task_flow(fake_storage) -> None:
    payload = {
        "title": "学情报告",
        "heat_map_data": {"分数加法": 0.9},
        "trend_data": [{"label": "第1周", "score": 80}],
        "weak_points": ["分数乘法"],
        "suggestions": ["多练习"],
    }
    resp = client.post("/render/report", json=payload)
    assert resp.status_code == 202, resp.text
    body = client.get(f"/tasks/{resp.json()['task_id']}").json()
    assert body["status"] == "completed"
    assert body["result"]["object_key"].endswith(".pdf")


def test_task_failed_status(fake_storage, monkeypatch) -> None:
    """渲染抛异常时任务流转为 failed 且带 error。"""

    def boom(_request):  # type: ignore[no-untyped-def]
        raise RuntimeError("render exploded")

    monkeypatch.setattr(jobs, "render_ppt", boom)
    resp = client.post("/render/ppt", json=_PPT_PAYLOAD)
    assert resp.status_code == 202
    body = client.get(f"/tasks/{resp.json()['task_id']}").json()
    assert body["status"] == "failed"
    assert "render exploded" in body["error"]


def test_task_not_found() -> None:
    reset_task_store_for_tests()
    resp = client.get("/tasks/nonexistent-task-id")
    assert resp.status_code == 404


def test_redis_unreachable_falls_back_to_memory(monkeypatch) -> None:
    """配置了不可达的 REDIS_URL 时，任务表降级为进程内存实现。"""
    from app.config import get_settings
    from app.tasks import MemoryTaskStore, get_task_store

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6390/0")  # 未监听端口，连接立即被拒
    get_settings.cache_clear()
    reset_task_store_for_tests()
    try:
        store = get_task_store()
        assert isinstance(store, MemoryTaskStore)
        # 降级后的内存表可正常流转任务状态
        record = store.create("ppt")
        record.status = "completed"
        store.update(record)
        assert store.get(record.task_id).status == "completed"
    finally:
        reset_task_store_for_tests()
        get_settings.cache_clear()


def test_healthz() -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["service"] == "docgen"
