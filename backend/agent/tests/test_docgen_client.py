"""docgen 客户端测试：契约适配（纯函数）与 提交→轮询 流程（MockTransport 离线）。"""

from __future__ import annotations

import httpx
import pytest

from app.services.docgen_client import (
    DocgenClient,
    DocgenError,
    adapt_payload,
)


class TestPayloadAdapt:
    def test_ppt(self) -> None:
        body = adapt_payload("ppt", {
            "topic": "一元二次方程", "style": "default",
            "slides": [{"slide_number": 1, "title": "封面", "content_points": ["课程介绍"],
                        "notes": "开场"}],
        })
        assert body["title"] == "一元二次方程"
        assert body["style"] == "minimal"  # 非法 style 回落
        assert body["slides"][0]["layout"] == "title"

    def test_lesson_plan(self) -> None:
        body = adapt_payload("lesson_plan", {
            "course_name": "配方法", "grade": "初三", "duration": 45,
            "template_type": "standard",
            "content": {"objectives": ["掌握配方法"],
                        "procedure": [{"stage": "导入", "duration_minutes": 5,
                                       "activities": "回顾"}]},
        })
        assert body["duration"] == "45分钟"  # int 分钟 → 字符串
        step = body["content"]["procedure"][0]
        assert step["duration"] == "5分钟" and step["teacher_activity"] == "回顾"

    def test_exam_paper(self) -> None:
        body = adapt_payload("exam_paper", {
            "grade": "初三", "subject": "数学",
            "questions": [
                {"number": 1, "type": "single_choice", "question": "q", "options": ["A", "B"],
                 "answer": "A", "score": 5},
                {"number": 2, "type": "short_answer", "question": "q2", "answer": "x", "score": 10},
            ],
        })
        assert body["questions"][0]["type"] == "choice"  # 题型映射
        assert body["questions"][1]["type"] == "essay"
        assert body["total_score"] == 15  # 自动汇总
        assert body["title"] == "初三数学试卷"

    def test_report(self) -> None:
        body = adapt_payload("report", {
            "class_name": "初三2班",
            "report": {"heat_map_data": [{"label": "方程", "value": 0.6}],
                       "trend_data": [{"date": "2026-08-01", "value": 12}],
                       "weak_points": ["分式方程"], "suggestions": ["加强变式训练"]},
        })
        assert body["heat_map_data"] == {"方程": 0.6}  # 列表 → 字典
        assert body["trend_data"] == [{"label": "2026-08-01", "score": 12.0}]
        assert body["title"] == "初三2班学情分析报告"

    def test_unknown_kind_passthrough(self) -> None:
        assert adapt_payload("other", {"a": 1}) == {"a": 1}


class TestRenderFlow:
    @staticmethod
    def _client(handler) -> DocgenClient:
        return DocgenClient(
            base_url="http://docgen:8005", token="t", poll_interval=0.01, poll_timeout=5,
            transport=httpx.MockTransport(handler),
        )

    async def test_submit_and_poll_until_completed(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            if request.url.path == "/render/ppt":
                assert request.headers["X-Internal-Token"] == "t"
                return httpx.Response(202, json={"task_id": "task-1", "status": "pending"})
            if request.url.path == "/tasks/task-1":
                if calls.count("GET /tasks/task-1") == 1:  # 首次轮询仍在渲染中
                    return httpx.Response(200, json={"task_id": "task-1", "task_type": "ppt",
                                                     "status": "processing", "result": None})
                return httpx.Response(200, json={
                    "task_id": "task-1", "task_type": "ppt", "status": "completed",
                    "result": {"file_url": "http://minio/ppt.pptx", "object_key": "k"},
                })
            return httpx.Response(404)

        result = await self._client(handler).render_ppt({"topic": "t", "slides": []})
        assert result["file_url"] == "http://minio/ppt.pptx"
        assert result["thumbnail_urls"] == []
        assert calls[0] == "POST /render/ppt"
        assert calls.count("GET /tasks/task-1") == 2

    async def test_sync_response_shortcut(self) -> None:
        """响应直接带 file_url（同步实现/测试桩）时跳过轮询。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"file_url": "http://minio/x.docx"})

        result = await self._client(handler).render_lesson_plan({"course_name": "c"})
        assert result["file_url"] == "http://minio/x.docx"

    async def test_analysis_maps_to_report_endpoint(self) -> None:
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            return httpx.Response(200, json={"file_url": "http://minio/r.pdf"})

        await self._client(handler).render_analysis({"class_name": "c", "report": {}})
        assert paths == ["/render/report"]  # analysis 旧名 → report

    async def test_exam_paper_dual_volume_normalized(self) -> None:
        """试卷任务返回学生/教师双卷，file_url 取学生卷。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "student_file_url": "http://minio/student.docx",
                "teacher_file_url": "http://minio/teacher.docx",
            })

        result = await self._client(handler).render_exam_paper({"questions": []})
        assert result["file_url"] == "http://minio/student.docx"
        assert result["teacher_file_url"] == "http://minio/teacher.docx"

    async def test_failed_task_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/render/ppt":
                return httpx.Response(202, json={"task_id": "task-9"})
            return httpx.Response(200, json={"task_id": "task-9", "task_type": "ppt",
                                             "status": "failed", "error": "模板损坏"})

        with pytest.raises(DocgenError, match="模板损坏"):
            await self._client(handler).render_ppt({"topic": "t"})

    async def test_bad_submit_response_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": True})

        with pytest.raises(DocgenError):
            await self._client(handler).render_ppt({"topic": "t"})
