"""文档生成服务（docgen）客户端。

docgen 实际契约（对齐 docgen/app/routers/render.py 与 tasks.py）：
- POST {DOCGEN_URL}/render/{kind}，kind ∈ ppt / lesson_plan / exam_paper / report，
  202 返回 {"task_id", "status": "pending"}，异步渲染；
- GET {DOCGEN_URL}/tasks/{task_id} 轮询，status=completed 时 result 内含文件地址。

本客户端对上层（content_gen）屏蔽异步细节：提交 → 轮询 → 归一化为
{"file_url", "thumbnail_urls", ...} 返回。kind=analysis 为旧名，映射到 report；
响应直接带 file_url 时（同步实现 / 测试桩）跳过轮询，保持兼容。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from eduagent_shared.config import Settings

DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_POLL_TIMEOUT_SECONDS = 600.0

# 旧 kind 名 → docgen 实际 kind
_KIND_MAP = {"analysis": "report"}

# docgen RenderPptRequest.style 仅接受这三个值，其余一律回落 minimal
_PPT_STYLES = {"minimal", "academic", "lively"}

# content_gen 题型 → docgen 题型（choice / blank / essay）
_EXAM_TYPE_MAP = {
    "single_choice": "choice",
    "multiple_choice": "choice",
    "true_false": "choice",
    "fill_blank": "blank",
    "short_answer": "essay",
    "calculation": "essay",
}


class DocgenError(RuntimeError):
    """docgen 渲染失败或响应格式异常。"""


# ---------------------------------------------------------------------------
# 请求体适配：content_gen 产出 → docgen 接口模型（纯函数，可单测）
# ---------------------------------------------------------------------------
def _adapt_ppt(payload: dict[str, Any]) -> dict[str, Any]:
    """{topic, grade, subject, style, slides} → docgen RenderPptRequest。"""
    style = str(payload.get("style") or "")
    slides = payload.get("slides") or []
    return {
        "title": payload.get("title") or payload.get("topic") or "未命名课件",
        "style": style if style in _PPT_STYLES else "minimal",
        "slides": [
            {
                "slide_number": int(s.get("slide_number") or i),
                "layout": "title" if i == 1 else "content",
                "title": str(s.get("title") or ""),
                "content_points": [str(p) for p in s.get("content_points") or []],
                "notes": str(s.get("notes") or ""),
            }
            for i, s in enumerate(slides, start=1)
        ],
    }


def _adapt_lesson_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """{course_name, grade, duration(分钟,int), content} → docgen RenderLessonPlanRequest。

    环节时长由 int 分钟转为 "N分钟" 字符串；LLM 的 activities 描述归入
    teacher_activity（docgen 的师生活动为分列结构）。
    """
    content = dict(payload.get("content") or {})
    duration = payload.get("duration")
    duration_text = duration if isinstance(duration, str) else f"{int(duration or 45)}分钟"
    procedure = [
        {
            "stage": str(p.get("stage") or ""),
            "duration": f"{int(p.get('duration_minutes') or 0)}分钟",
            "teacher_activity": str(p.get("activities") or ""),
            "student_activity": "",
            "design_intent": "",
        }
        for p in content.get("procedure") or []
    ]
    course_name = payload.get("course_name") or payload.get("title") or "未命名教案"
    return {
        "title": str(payload.get("title") or course_name),
        "course_name": str(course_name),
        "grade": str(payload.get("grade") or ""),
        "subject": str(payload.get("subject") or ""),
        "duration": duration_text,
        "template_type": str(payload.get("template_type") or "standard"),
        "content": {
            "objectives": [str(o) for o in content.get("objectives") or []],
            "key_points": [str(k) for k in content.get("key_points") or []],
            "difficulties": [str(d) for d in content.get("difficulties") or []],
            "procedure": procedure,
            "board_design": str(content.get("board_design") or ""),
            "homework": str(content.get("homework") or ""),
            "reflection": str(content.get("reflection") or ""),
        },
    }


def _adapt_exam_paper(payload: dict[str, Any]) -> dict[str, Any]:
    """{grade, subject, questions} → docgen RenderExamPaperRequest（题型映射 + 汇总分值）。"""
    questions = [
        {
            "number": int(q.get("number") or i),
            "type": _EXAM_TYPE_MAP.get(str(q.get("type") or ""), "essay"),
            "question": str(q.get("question") or ""),
            "options": [str(o) for o in q.get("options") or []],
            "answer": str(q.get("answer") or ""),
            "analysis": str(q.get("analysis") or ""),
            "knowledge_tag": str(q.get("knowledge_tag") or ""),
            "score": float(q.get("score") or 0),
        }
        for i, q in enumerate(payload.get("questions") or [], start=1)
    ]
    total_score = float(payload.get("total_score") or sum(q["score"] for q in questions) or 100)
    grade = str(payload.get("grade") or "")
    subject = str(payload.get("subject") or "")
    return {
        "title": str(payload.get("title") or f"{grade}{subject}试卷"),
        "grade": grade,
        "subject": subject,
        "duration_minutes": int(payload.get("duration_minutes") or 90),
        "total_score": total_score,
        "questions": questions,
    }


def _adapt_report(payload: dict[str, Any]) -> dict[str, Any]:
    """{class_name, report} → docgen RenderReportRequest（列表型图表数据转 docgen 结构）。"""
    report = dict(payload.get("report") or {})
    heat_map = {
        str(item.get("label", "")): float(item.get("value", 0) or 0)
        for item in report.get("heat_map_data") or []
        if isinstance(item, dict)
    }
    trend = [
        {
            "label": str(point.get("date") or point.get("label") or ""),
            "score": float(point.get("value", point.get("score", 0)) or 0),
        }
        for point in report.get("trend_data") or []
        if isinstance(point, dict)
    ]
    class_name = str(payload.get("class_name") or "")
    return {
        "title": str(payload.get("title") or f"{class_name}学情分析报告"),
        "heat_map_data": heat_map,
        "trend_data": trend,
        "weak_points": [str(w) for w in report.get("weak_points") or []],
        "suggestions": [str(s) for s in report.get("suggestions") or []],
    }


_PAYLOAD_ADAPTERS = {
    "ppt": _adapt_ppt,
    "lesson_plan": _adapt_lesson_plan,
    "exam_paper": _adapt_exam_paper,
    "report": _adapt_report,
}


def adapt_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """按 docgen 接口模型适配请求体；未知 kind 原样透传。"""
    adapter = _PAYLOAD_ADAPTERS.get(kind)
    return adapter(payload) if adapter is not None else payload


def _normalize_result(kind: str, result: dict[str, Any]) -> dict[str, Any]:
    """把 docgen 任务 result 归一化为 {"file_url", "thumbnail_urls", ...}。

    试卷任务产出学生卷/教师卷两份文件，file_url 取学生卷。
    """
    data = dict(result)
    file_url = data.get("file_url") or data.get("student_file_url")
    if not file_url:
        raise DocgenError(f"docgen 结果缺少 file_url: {str(result)[:200]}")
    data["file_url"] = file_url
    data.setdefault("thumbnail_urls", [])
    return data


class DocgenClient:
    """docgen httpx 封装：提交渲染 → 轮询任务 → 归一化结果。"""

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 180.0,
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        poll_timeout: float = DEFAULT_POLL_TIMEOUT_SECONDS,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "X-Internal-Token": token,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout
        self._transport = transport  # 测试注入 MockTransport，生产为 None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=self._timeout,
            transport=self._transport,
        )

    async def render(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """提交渲染并等待完成，返回 {"file_url", "thumbnail_urls", ...}。"""
        kind = _KIND_MAP.get(kind, kind)
        body = adapt_payload(kind, payload)
        async with self._client() as client:
            resp = await client.post(f"/render/{kind}", json=body)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise DocgenError(f"docgen 响应格式异常: {str(data)[:200]}")
            if "file_url" in data or "student_file_url" in data:
                result = data  # 同步实现 / 测试桩：直接返回结果
            elif "task_id" in data:
                result = await self._poll_task(client, str(data["task_id"]))
            else:
                raise DocgenError(f"docgen 响应缺少 task_id/file_url: {str(data)[:200]}")
        return _normalize_result(kind, result)

    async def _poll_task(self, client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
        """轮询 GET /tasks/{task_id} 直至 completed / failed / 超时。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._poll_timeout
        while True:
            resp = await client.get(f"/tasks/{task_id}")
            resp.raise_for_status()
            status = resp.json()
            state = str(status.get("status") or "")
            if state == "completed":
                result = status.get("result")
                if not isinstance(result, dict):
                    raise DocgenError(f"docgen 任务结果格式异常: {str(status)[:200]}")
                return result
            if state == "failed":
                raise DocgenError(str(status.get("error") or "docgen 渲染失败"))
            if loop.time() >= deadline:
                raise DocgenError(f"docgen 任务 {task_id} 渲染超时")
            await asyncio.sleep(self._poll_interval)

    async def render_ppt(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.render("ppt", payload)

    async def render_lesson_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.render("lesson_plan", payload)

    async def render_exam_paper(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.render("exam_paper", payload)

    async def render_analysis(self, payload: dict[str, Any]) -> dict[str, Any]:
        """学情分析报告（docgen 侧 kind 为 report）。"""
        return await self.render("analysis", payload)


def get_docgen_client(settings: Settings) -> DocgenClient:
    return DocgenClient(
        base_url=settings.docgen_url,
        token=settings.internal_service_token,
    )
