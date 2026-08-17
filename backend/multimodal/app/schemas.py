"""多模态服务对外接口模型（Pydantic v2）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------- OCR ----------


class OcrBox(BaseModel):
    """文字区域框，坐标为图片像素坐标。"""

    x: int
    y: int
    w: int
    h: int
    text: str


class OcrRequest(BaseModel):
    image_url: str | None = Field(default=None, description="图片 URL，与 multipart 二选一")
    grayscale: bool = Field(default=False, description="是否灰度预处理")


class OcrResponse(BaseModel):
    text: str
    latex_blocks: list[str] = Field(default_factory=list, description="识别出的数学公式 LaTeX")
    boxes: list[OcrBox] = Field(default_factory=list)


# ---------- ASR ----------


class AsrSegment(BaseModel):
    start: float = Field(description="段起始时间（秒）")
    end: float = Field(description="段结束时间（秒）")
    text: str


class AsrRequest(BaseModel):
    audio_url: str | None = Field(default=None, description="音频 URL，与 multipart 二选一")
    language: str | None = Field(default=None, description="期望语言，如 zh/en；为空自动检测")


class AsrResponse(BaseModel):
    text: str
    segments: list[AsrSegment] = Field(default_factory=list)
    language: str


# ---------- TTS ----------


class TtsRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str = Field(default="default", description="音色标识")
    speed: float = Field(default=1.0, gt=0.25, le=4.0, description="语速倍率")


# ---------- 文档解析 ----------


class ParseDocumentRequest(BaseModel):
    file_url: str | None = Field(default=None, description="文件 URL，与 multipart 二选一")
    file_type: str | None = Field(
        default=None, description="pdf/docx/txt；multipart 时从文件名推断"
    )
    auto_split_questions: bool = Field(default=False, description="试卷模式：按题号正则分题")


class DocumentSection(BaseModel):
    heading: str
    paragraphs: list[str] = Field(default_factory=list)
    page: int | None = None


class DocumentQuestion(BaseModel):
    """试卷模式切分出的单题。"""

    number: str
    text: str
    page: int | None = None


class ParseDocumentResponse(BaseModel):
    title: str
    sections: list[DocumentSection] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list, description="文档内图片（占位标识或 URL）")
    questions: list[DocumentQuestion] = Field(
        default_factory=list, description="auto_split_questions=true 时的分题结果"
    )


# ---------- 作业批改（结构化比对，不含归因） ----------


class GradeRequest(BaseModel):
    image_url: str = Field(description="作业照片 URL，与 multipart 二选一")
    subject: str = Field(default="math", description="学科，用于选择比对规则")
    standard_answer: dict[str, str] | None = Field(
        default=None, description="标准答案：题号 -> 答案文本；为空则仅返回 OCR 结果"
    )


class GradeMark(BaseModel):
    """前端 SVG 叠加用的圈注数据。"""

    type: str = Field(description="correct / wrong / partial")
    x: int
    y: int
    radius: int
    comment: str = ""


class GradeItem(BaseModel):
    question_box: OcrBox
    extracted_answer: str
    is_correct: bool | None = Field(
        default=None, description="无标准答案时为 None（仅识别未比对）"
    )
    correct_answer: str | None = None
    marks: list[GradeMark] = Field(default_factory=list)


class GradeResponse(BaseModel):
    subject: str
    items: list[GradeItem] = Field(default_factory=list)
