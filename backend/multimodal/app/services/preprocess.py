"""图片预处理：EXIF 旋转校正、缩放、灰度（pillow）。"""

from __future__ import annotations

import io

from PIL import Image, ImageOps

MAX_DIMENSION = 2048


def preprocess_image(
    image_bytes: bytes,
    *,
    grayscale: bool = False,
    max_dimension: int = MAX_DIMENSION,
) -> bytes:
    """对上传图片做标准化预处理，返回 PNG 字节。

    - 按 EXIF 方向信息旋转/翻转到正立；
    - 长边超过 max_dimension 时等比缩小；
    - 可选灰度化（OCR 前常用）。
    """
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)

    if max(img.size) > max_dimension:
        scale = max_dimension / max(img.size)
        new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        img = img.resize(new_size, Image.LANCZOS)

    if grayscale:
        img = img.convert("L")

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def image_size(image_bytes: bytes) -> tuple[int, int]:
    """读取图片尺寸（宽、高），供坐标计算使用。"""
    img = Image.open(io.BytesIO(image_bytes))
    return img.size
