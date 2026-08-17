"""文档解析路由：pdf/docx/txt 结构化提取，试卷模式按题号分题。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.deps import verify_internal_token
from app.routers.common import guess_file_type, resolve_bytes
from app.schemas import ParseDocumentResponse
from app.services.document import parse_document

router = APIRouter(dependencies=[Depends(verify_internal_token)])


@router.post("/parse-document", response_model=ParseDocumentResponse)
async def parse_document_route(
    file: Annotated[UploadFile | None, File()] = None,
    file_url: Annotated[str | None, Form()] = None,
    file_type: Annotated[str | None, Form()] = None,
    auto_split_questions: Annotated[bool, Form()] = False,
) -> ParseDocumentResponse:
    """解析文档为 {title, sections, images}，试卷模式附加 questions。"""
    raw = await resolve_bytes(file, file_url, "文档")
    ftype = (file_type or guess_file_type(file.filename if file else None)).lower()
    if not ftype:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "无法确定文件类型，请显式传 file_type"
        )
    try:
        return parse_document(raw, ftype, auto_split_questions=auto_split_questions)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
