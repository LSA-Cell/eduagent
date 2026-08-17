"""OCR 路由：图片文字/公式识别。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.deps import verify_internal_token
from app.providers import get_ocr_provider
from app.routers.common import resolve_bytes
from app.schemas import OcrBox, OcrResponse
from app.services.preprocess import preprocess_image

router = APIRouter(dependencies=[Depends(verify_internal_token)])


@router.post("/ocr", response_model=OcrResponse)
async def ocr(
    file: Annotated[UploadFile | None, File()] = None,
    image_url: Annotated[str | None, Form()] = None,
    grayscale: Annotated[bool, Form()] = False,
) -> OcrResponse:
    """识别图片中的文字与数学公式（LaTeX），返回坐标框。

    支持 multipart 图片上传或表单字段 image_url 二选一。
    """
    raw = await resolve_bytes(file, image_url, "图片")
    processed = preprocess_image(raw, grayscale=grayscale)
    result = await get_ocr_provider().recognize(processed)
    return OcrResponse(
        text=result.text,
        latex_blocks=result.latex_blocks,
        boxes=[OcrBox(**b) for b in result.boxes],
    )
