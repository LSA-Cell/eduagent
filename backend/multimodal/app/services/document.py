"""文档解析：pypdf / python-docx 提取结构化内容，支持试卷按题号分题。"""

from __future__ import annotations

import io
import re

from pypdf import PdfReader

from app.schemas import DocumentQuestion, DocumentSection, ParseDocumentResponse

# 题号正则：1. 1、1． （1） 第1题 一、
QUESTION_PATTERN = re.compile(
    r"^\s*(?:第\s*(\d+)\s*题|(\d+)\s*[.、．]|\((\d+)\)|（(\d+)）|([一二三四五六七八九十]+)、)"
)

_CHINESE_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def parse_document(
    file_bytes: bytes,
    file_type: str,
    *,
    auto_split_questions: bool = False,
) -> ParseDocumentResponse:
    """按文件类型解析文档为结构化 sections，可选试卷分题。"""
    file_type = file_type.lower().lstrip(".")
    if file_type == "pdf":
        title, sections = _parse_pdf(file_bytes)
    elif file_type == "docx":
        title, sections = _parse_docx(file_bytes)
    elif file_type == "txt":
        title, sections = _parse_txt(file_bytes)
    else:
        raise ValueError(f"不支持的文件类型: {file_type}")

    images = _extract_images(file_bytes, file_type)
    resp = ParseDocumentResponse(title=title, sections=sections, images=images)
    if auto_split_questions:
        resp.questions = split_questions(sections)
    return resp


def split_questions(sections: list[DocumentSection]) -> list[DocumentQuestion]:
    """按题号正则将段落流切分为题目列表（试卷模式）。"""
    questions: list[DocumentQuestion] = []
    current_number: str | None = None
    current_lines: list[str] = []
    current_page: int | None = None

    def flush() -> None:
        nonlocal current_number, current_lines, current_page
        if current_number is not None:
            questions.append(
                DocumentQuestion(
                    number=current_number,
                    text="\n".join(current_lines).strip(),
                    page=current_page,
                )
            )
        current_number, current_lines, current_page = None, [], None

    for section in sections:
        for para in section.paragraphs:
            m = QUESTION_PATTERN.match(para)
            if m:
                flush()
                digits = next(g for g in m.groups() if g)
                current_number = (
                    str(_CHINESE_NUM.get(digits, digits)) if not digits.isdigit() else digits
                )
                current_page = section.page
                # 题号后的题干文本
                current_lines = [QUESTION_PATTERN.sub("", para, count=1).strip()]
            elif current_number is not None:
                current_lines.append(para)
    flush()
    return questions


# ---------- 内部实现 ----------


def _parse_pdf(file_bytes: bytes) -> tuple[str, list[DocumentSection]]:
    reader = PdfReader(io.BytesIO(file_bytes))
    sections: list[DocumentSection] = []
    for page_no, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        paragraphs = [ln.strip() for ln in text.splitlines() if ln.strip()]
        heading = paragraphs[0] if paragraphs else f"第 {page_no} 页"
        sections.append(
            DocumentSection(heading=heading, paragraphs=paragraphs, page=page_no)
        )
    title = ""
    meta = reader.metadata
    if meta and meta.title:
        title = meta.title
    if not title and sections:
        title = sections[0].heading
    return title or "未命名文档", sections


def _parse_docx(file_bytes: bytes) -> tuple[str, list[DocumentSection]]:
    import docx

    doc = docx.Document(io.BytesIO(file_bytes))
    sections: list[DocumentSection] = []
    current: DocumentSection | None = None
    title = "未命名文档"
    title_found = False

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "").lower() if para.style else ""
        if not title_found and ("title" in style or "标题" in style):
            title = text
            title_found = True
            continue
        if style.startswith("heading") or style.startswith("标题"):
            current = DocumentSection(heading=text, paragraphs=[], page=None)
            sections.append(current)
        else:
            if current is None:
                current = DocumentSection(heading="正文", paragraphs=[], page=None)
                sections.append(current)
            current.paragraphs.append(text)

    if not sections and title_found:
        sections = [DocumentSection(heading=title, paragraphs=[], page=None)]
    return title, sections


def _parse_txt(file_bytes: bytes) -> tuple[str, list[DocumentSection]]:
    text = file_bytes.decode("utf-8", errors="replace")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    title = lines[0] if lines else "未命名文档"
    section = DocumentSection(heading=title, paragraphs=lines[1:] if len(lines) > 1 else [], page=1)
    return title, [section]


def _extract_images(file_bytes: bytes, file_type: str) -> list[str]:
    """提取文档内嵌图片的占位标识列表。

    图片本体不落库也不上传——按服务边界，这里只返回数量与位置标识，
    由调用方决定是否需要另行取图。
    """
    images: list[str] = []
    if file_type == "docx":
        import docx

        doc = docx.Document(io.BytesIO(file_bytes))
        idx = 0
        for rel in doc.part.rels.values():
            if "image" in rel.reltype:
                idx += 1
                images.append(f"docx-image-{idx}")
    elif file_type == "pdf":
        reader = PdfReader(io.BytesIO(file_bytes))
        for page_no, page in enumerate(reader.pages, start=1):
            try:
                for idx, _ in enumerate(page.images, start=1):
                    images.append(f"pdf-p{page_no}-image-{idx}")
            except Exception:
                continue
    return images
