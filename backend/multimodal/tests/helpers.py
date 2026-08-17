"""测试公共工具：构造内存中的测试图片与文档字节。"""

from __future__ import annotations

import io

from PIL import Image


def make_image_bytes(width: int = 800, height: int = 600, exif_orientation: int | None = None) -> bytes:
    """生成纯色素材图片（JPEG），可选写入 EXIF 方向标记。"""
    img = Image.new("RGB", (width, height), (200, 200, 200))
    buf = io.BytesIO()
    if exif_orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation  # Orientation 标签
        img.save(buf, format="JPEG", exif=exif)
    else:
        img.save(buf, format="JPEG")
    return buf.getvalue()


def make_pdf_bytes(lines: list[str]) -> bytes:
    """构造最小可提取文本的单页 PDF（含合法 xref 表与 startxref，pypdf 严格模式可解析）。"""
    content_lines = ["BT /F1 12 Tf 72 720 Td"]
    for i, line in enumerate(lines):
        if i > 0:
            content_lines.append("0 -20 Td")
        content_lines.append(f"({line}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for num, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{num} 0 obj ".encode() + body + b" endobj\n"

    xref_pos = len(pdf)
    count = len(objects) + 1
    pdf += f"xref\n0 {count}\n".encode()
    pdf += b"0000000000 65535 f \n"
    for off in offsets:
        pdf += f"{off:010d} 00000 n \n".encode()
    pdf += f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    return bytes(pdf)


def make_docx_bytes(title: str, blocks: list[tuple[str, str]]) -> bytes:
    """构造内存 docx：title 用 Title 样式，blocks 为 (样式, 文本) 列表。

    样式取值："heading1" / "para"。
    """
    import docx

    doc = docx.Document()
    doc.add_heading(title, 0)
    for style, text in blocks:
        if style == "heading1":
            doc.add_heading(text, level=1)
        else:
            doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
