"""业务代理路由：错题本 / 记忆卡片 / 学习计划 / 知识库 / 教师内容 / 学情 / 口语。

统一透传到 Agent 编排服务（AGENT_ORCHESTRATOR_URL），BFF 只做鉴权与转发，
不含业务逻辑。路径与下游保持一致。

路由表 _ROUTE_SPECS 显式登记（方法, 路径, 是否仅教师），泛化处理由
_make_proxy 统一完成：{item_id} 路径参数替换、query 透传、JSON body 透传。
注意：/mistakes/{id}/variations 下游为 SSE，这里按整体缓冲返回（非逐 chunk 流式），
文本量小可接受；如需真流式再引入流式转发。
"""

from __future__ import annotations

import json
import re

import httpx
from fastapi import APIRouter, Depends, Request

from ..deps import get_current_user, get_http_client, require_teacher
from ..errors import AppError
from ..services.forward import forward_to_agent

router = APIRouter(tags=["business"])

# (方法, 路径, 是否仅教师)；同一资源内字面量子路径须排在 {item_id} 之前
_ROUTE_SPECS: list[tuple[str, str, bool]] = [
    # 聊天媒体直传（仅鉴权后透传，文件流不经过 BFF）
    ("POST", "/uploads/presign", False),
    ("POST", "/uploads/confirm", False),
    # 错题本
    ("GET", "/mistakes", False),
    ("GET", "/mistakes/{item_id}", False),
    ("POST", "/mistakes/{item_id}/review", False),
    ("POST", "/mistakes/{item_id}/variations", False),
    # 记忆卡片
    ("GET", "/flashcards", False),
    ("POST", "/flashcards", False),
    ("POST", "/flashcards/generate", False),
    ("GET", "/flashcards/{item_id}", False),
    ("PATCH", "/flashcards/{item_id}", False),
    ("DELETE", "/flashcards/{item_id}", False),
    ("POST", "/flashcards/{item_id}/review", False),
    # 学习计划
    ("GET", "/study-plans", False),
    ("POST", "/study-plans", False),
    ("GET", "/study-plans/{item_id}", False),
    ("POST", "/study-plans/{item_id}/checkin", False),
    # 知识库文档
    ("GET", "/kb/documents", False),
    ("GET", "/kb/documents/{item_id}", False),
    ("POST", "/kb/documents/{item_id}/confirm", False),
    ("DELETE", "/kb/documents/{item_id}", False),
    # 教师内容生成（POST 创建 202 + GET 列表/详情轮询）
    ("GET", "/teacher/lesson-plans", True),
    ("POST", "/teacher/lesson-plans", True),
    ("GET", "/teacher/lesson-plans/{item_id}", True),
    ("GET", "/teacher/presentations", True),
    ("POST", "/teacher/presentations", True),
    ("GET", "/teacher/presentations/{item_id}", True),
    ("GET", "/teacher/exam-papers", True),
    ("POST", "/teacher/exam-papers", True),
    ("GET", "/teacher/exam-papers/{item_id}", True),
    ("GET", "/teacher/learning-analyses", True),
    ("POST", "/teacher/learning-analyses", True),
    ("GET", "/teacher/learning-analyses/{item_id}", True),
    # 学情看板（知识点掌握度矩阵）
    ("GET", "/analytics/knowledge-mastery", False),
    # 口语对练评分
    ("POST", "/voice/evaluate", False),
]


def _make_proxy(method: str, path: str):
    async def handler(
        request: Request,
        user=Depends(get_current_user),
        client: httpx.AsyncClient = Depends(get_http_client),
    ):
        item_id = request.path_params.get("item_id")
        downstream = path if item_id is None else path.replace("{item_id}", str(item_id))
        body = None
        if method not in ("GET", "DELETE"):
            raw = await request.body()
            if raw:
                body = json.loads(raw)
        return await forward_to_agent(
            client,
            downstream,
            user_id=str(user.id),
            method=method,
            params=dict(request.query_params),
            json_body=body,
        )

    return handler


for _method, _path, _teacher_only in _ROUTE_SPECS:
    _deps = [Depends(require_teacher)] if _teacher_only else []
    router.add_api_route(
        _path,
        _make_proxy(_method, _path),
        methods=[_method],
        dependencies=_deps,
        summary=f"{_method} {_path}",
    )


@router.post("/kb/documents/upload-url")
async def create_upload_url(
    request: Request,
    user=Depends(get_current_user),
    client: httpx.AsyncClient = Depends(get_http_client),
):
    """知识库文件上传预签名：转发 Agent 编排服务（封装 MinIO 预签名）并原样返回。"""
    body = await request.json()
    return await forward_to_agent(
        client,
        "/kb/documents/upload-url",
        user_id=str(user.id),
        method="POST",
        json_body=body,
    )


@router.get("/files/{object_key:path}/url")
async def get_file_url(
    object_key: str,
    user=Depends(get_current_user),
    client: httpx.AsyncClient = Depends(get_http_client),
):
    """转发私有文件下载链接请求；归属与对象校验由 Agent 完成。"""
    # 先限制路径字符，避免 httpx 规范化 ../、?、# 后访问其他内部路由。
    if not re.fullmatch(
        rf"u/{re.escape(str(user.id))}/[0-9a-f]{{32}}\.(?:jpg|jpeg|png|webp|m4a|wav|mp3|ogg)",
        object_key,
    ):
        raise AppError(404, "not_found", "文件不存在")
    return await forward_to_agent(
        client, f"/files/{object_key}/url", user_id=str(user.id), method="GET"
    )
