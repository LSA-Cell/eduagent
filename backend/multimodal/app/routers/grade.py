"""作业批改路由：OCR + 规则比对，返回结构化比对数据与坐标圈注。

注意：本接口只做「识别 + 比对 + 圈注坐标」，学科归因/讲解由调用方完成。
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.deps import verify_internal_token
from app.providers import get_ocr_provider
from app.routers.common import resolve_bytes
from app.schemas import GradeResponse
from app.services.grading import grade_from_ocr
from app.services.preprocess import preprocess_image

router = APIRouter(dependencies=[Depends(verify_internal_token)])


@router.post("/grade-homework", response_model=GradeResponse)
async def grade_homework(
    file: Annotated[UploadFile | None, File()] = None,
    image_url: Annotated[str | None, Form()] = None,
    subject: Annotated[str, Form()] = "math",
    standard_answer: Annotated[str | None, Form()] = None,
) -> GradeResponse:
    """识别作业照片并逐题比对，返回 marks 供前端 SVG 叠加圈注。

    standard_answer 为 JSON 字符串：{"1": "8", "2": "3"}，可选。
    """
    raw = await resolve_bytes(file, image_url, "作业图片")
    processed = preprocess_image(raw)
    ocr = await get_ocr_provider().recognize(processed)

    answers: dict[str, str] | None = None
    if standard_answer:
        try:
            answers = json.loads(standard_answer)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "standard_answer 不是合法 JSON"
            ) from exc
    return grade_from_ocr(ocr, subject, answers)
