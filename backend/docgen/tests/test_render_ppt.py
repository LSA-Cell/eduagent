"""PPT 渲染器测试：三种风格生成真实文件并读回校验。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pptx import Presentation

from app.schemas import RenderPptRequest, SlideSpec
from app.services.render_ppt import render_ppt


def _make_request(style: str) -> RenderPptRequest:
    return RenderPptRequest(
        title="分数的加法",
        style=style,  # type: ignore[arg-type]
        slides=[
            SlideSpec(slide_number=1, layout="title", title="分数的加法", content_points=["三年级数学 · 第 7 单元"]),
            SlideSpec(slide_number=2, layout="toc", title="目录", content_points=["同分母加法", "通分", "练习"]),
            SlideSpec(slide_number=3, layout="content", title="同分母加法", content_points=["分母不变，分子相加", "例：1/4 + 2/4 = 3/4"], notes="强调分母不变"),
            SlideSpec(slide_number=4, layout="summary", title="小结", content_points=["掌握了同分母分数加法"]),
        ],
    )


@pytest.mark.parametrize("style", ["minimal", "academic", "lively"])
def test_render_ppt_each_style(tmp_path: Path, style: str) -> None:
    data = render_ppt(_make_request(style))
    out = tmp_path / f"deck-{style}.pptx"
    out.write_bytes(data)

    assert out.stat().st_size > 0
    prs = Presentation(str(out))
    assert len(prs.slides) == 4

    # 内容页应包含要点文本
    content_slide = prs.slides[2]
    texts = [
        run.text
        for shape in content_slide.shapes
        if shape.has_text_frame
        for para in shape.text_frame.paragraphs
        for run in para.runs
    ]
    joined = "".join(texts)
    assert "同分母加法" in joined
    assert "分母不变，分子相加" in joined

    # 备注写入校验
    notes = prs.slides[2].notes_slide.notes_text_frame.text
    assert notes == "强调分母不变"


def test_render_ppt_slide_ordering(tmp_path: Path) -> None:
    """slide_number 乱序输入应按序号排序渲染。"""
    req = _make_request("minimal")
    req.slides = list(reversed(req.slides))
    data = render_ppt(req)
    out = tmp_path / "deck.pptx"
    out.write_bytes(data)
    prs = Presentation(str(out))
    first_texts = "".join(
        run.text
        for shape in prs.slides[0].shapes
        if shape.has_text_frame
        for para in shape.text_frame.paragraphs
        for run in para.runs
    )
    assert "分数的加法" in first_texts  # 第 1 页是标题页
