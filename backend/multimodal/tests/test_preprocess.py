"""图片预处理测试：EXIF 旋转、缩放、灰度。"""

from __future__ import annotations

import io

from PIL import Image

from app.services.preprocess import preprocess_image
from tests.helpers import make_image_bytes


def test_preprocess_resize_large_image() -> None:
    raw = make_image_bytes(4000, 1000)
    out = preprocess_image(raw, max_dimension=2048)
    img = Image.open(io.BytesIO(out))
    assert max(img.size) <= 2048
    # 等比缩放保持宽高比 4:1
    assert img.width == 2048 and img.height == 512


def test_preprocess_exif_rotation() -> None:
    # EXIF Orientation=6（需顺时针旋转 90°），宽高应互换
    raw = make_image_bytes(100, 50, exif_orientation=6)
    out = preprocess_image(raw)
    img = Image.open(io.BytesIO(out))
    assert img.size == (50, 100)


def test_preprocess_grayscale() -> None:
    raw = make_image_bytes(200, 200)
    out = preprocess_image(raw, grayscale=True)
    img = Image.open(io.BytesIO(out))
    assert img.mode == "L"


def test_preprocess_output_is_png() -> None:
    raw = make_image_bytes()
    out = preprocess_image(raw)
    img = Image.open(io.BytesIO(out))
    assert img.format == "PNG"


def test_preprocess_small_image_unchanged_size() -> None:
    raw = make_image_bytes(320, 240)
    out = preprocess_image(raw)
    img = Image.open(io.BytesIO(out))
    assert img.size == (320, 240)
