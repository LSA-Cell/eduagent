"""学情报告渲染器：reportlab 生成 PDF（中文用内置 CID 字体 STSong-Light）。"""

from __future__ import annotations

import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.schemas import RenderReportRequest

_CJK_FONT = "STSong-Light"
_font_registered = False


def _ensure_cjk_font() -> None:
    """注册 reportlab 内置 CID 中文字体（无需外部字体文件）。"""
    global _font_registered
    if not _font_registered:
        pdfmetrics.registerFont(UnicodeCIDFont(_CJK_FONT))
        _font_registered = True


def render_report(request: RenderReportRequest) -> bytes:
    """渲染学情报告 PDF，返回文件字节。"""
    _ensure_cjk_font()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=20 * mm, bottomMargin=20 * mm, title=request.title,
    )
    title_style = ParagraphStyle("title", fontName=_CJK_FONT, fontSize=20, leading=28)
    h2 = ParagraphStyle("h2", fontName=_CJK_FONT, fontSize=14, leading=20, spaceBefore=12)
    body = ParagraphStyle("body", fontName=_CJK_FONT, fontSize=11, leading=16)

    story: list = [Paragraph(request.title, title_style), Spacer(1, 8 * mm)]

    if request.heat_map_data:
        story.append(Paragraph("知识点掌握热力", h2))
        rows = [["知识点", "掌握率"]]
        rows.extend(
            [name, f"{rate * 100:.0f}%"] for name, rate in request.heat_map_data.items()
        )
        table = Table(rows, colWidths=[100 * mm, 40 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), _CJK_FONT),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("GRID", (0, 0), (-1, -1), 0.5, (0.7, 0.7, 0.7)),
                    ("BACKGROUND", (0, 0), (-1, 0), (0.93, 0.93, 0.93)),
                ]
            )
        )
        story.append(table)

    if request.trend_data:
        story.append(Paragraph("成绩趋势", h2))
        rows = [["时间点", "成绩"]]
        rows.extend([p.label, f"{p.score:g}"] for p in request.trend_data)
        table = Table(rows, colWidths=[60 * mm, 40 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), _CJK_FONT),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("GRID", (0, 0), (-1, -1), 0.5, (0.7, 0.7, 0.7)),
                    ("BACKGROUND", (0, 0), (-1, 0), (0.93, 0.93, 0.93)),
                ]
            )
        )
        story.append(table)

    if request.weak_points:
        story.append(Paragraph("薄弱知识点", h2))
        for wp in request.weak_points:
            story.append(Paragraph(f"• {wp}", body))

    if request.suggestions:
        story.append(Paragraph("学习建议", h2))
        for i, sg in enumerate(request.suggestions, start=1):
            story.append(Paragraph(f"{i}. {sg}", body))

    doc.build(story)
    return buf.getvalue()
