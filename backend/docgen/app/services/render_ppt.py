"""PPT 渲染器：python-pptx 按风格常量渲染标题页/目录页/内容页/总结页。"""

from __future__ import annotations

import io

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt

from app.schemas import RenderPptRequest, SlideSpec
from app.templates.styles import StyleTheme, get_style

SLIDE_W = Inches(13.333)  # 16:9
SLIDE_H = Inches(7.5)


def render_ppt(request: RenderPptRequest) -> bytes:
    """渲染完整 PPTX，返回文件字节。"""
    theme = get_style(request.style)
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    blank = prs.slide_layouts[6]  # 空白版式，全部元素手工放置

    for spec in sorted(request.slides, key=lambda s: s.slide_number):
        slide = prs.slides.add_slide(blank)
        _fill_background(slide, theme)
        if spec.layout == "title":
            _render_title_slide(slide, spec, request.title, theme)
        else:
            _render_text_slide(slide, spec, theme)
        if spec.notes:
            slide.notes_slide.notes_text_frame.text = spec.notes

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _fill_background(slide, theme: StyleTheme) -> None:
    """整页背景色填充。"""
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = RGBColor.from_string(theme.bg_color)


def _add_textbox(slide, left, top, width, height):  # type: ignore[no-untyped-def]
    return slide.shapes.add_textbox(left, top, width, height)


def _style_run(run, theme: StyleTheme, *, size: int, color: str, bold: bool) -> None:
    font = run.font
    font.name = theme.body_font
    font.size = Pt(size)
    font.bold = bold
    font.color.rgb = RGBColor.from_string(color)


def _render_title_slide(slide, spec: SlideSpec, deck_title: str, theme: StyleTheme) -> None:
    """标题页：主标题居中 + 副标题（若有要点取首条）。"""
    box = _add_textbox(slide, Inches(1), Inches(2.6), SLIDE_W - Inches(2), Inches(1.6))
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = spec.title or deck_title
    _style_run(run, theme, size=theme.title_size_pt + 10, color=theme.title_color, bold=True)
    run.font.name = theme.title_font

    if spec.content_points:
        sub = _add_textbox(slide, Inches(1), Inches(4.2), SLIDE_W - Inches(2), Inches(1.0))
        p2 = sub.text_frame.paragraphs[0]
        run2 = p2.add_run()
        run2.text = spec.content_points[0]
        _style_run(run2, theme, size=theme.body_size_pt + 2, color=theme.accent_color, bold=False)


def _render_text_slide(slide, spec: SlideSpec, theme: StyleTheme) -> None:
    """目录/内容/总结页：页首标题条 + 项目符号要点。"""
    # 标题条
    title_box = _add_textbox(slide, Inches(0.6), Inches(0.4), SLIDE_W - Inches(1.2), Inches(1.0))
    tp = title_box.text_frame.paragraphs[0]
    run = tp.add_run()
    run.text = spec.title
    _style_run(run, theme, size=theme.title_size_pt, color=theme.title_color, bold=True)
    run.font.name = theme.title_font

    # 标题下装饰线（强调色）
    line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.6), Inches(1.35), Inches(2.0), Pt(4))
    line.fill.solid()
    line.fill.fore_color.rgb = RGBColor.from_string(theme.accent_color)
    line.line.fill.background()
    line.shadow.inherit = False

    # 要点
    body = _add_textbox(slide, Inches(0.9), Inches(1.8), SLIDE_W - Inches(1.8), Inches(5.2))
    tf = body.text_frame
    tf.word_wrap = True
    for idx, point in enumerate(spec.content_points):
        p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
        p.space_after = Pt(8)
        bullet = p.add_run()
        bullet.text = f"{theme.bullet_char} "
        _style_run(bullet, theme, size=theme.body_size_pt, color=theme.accent_color, bold=True)
        text = p.add_run()
        text.text = point
        _style_run(text, theme, size=theme.body_size_pt, color=theme.body_color, bold=False)
