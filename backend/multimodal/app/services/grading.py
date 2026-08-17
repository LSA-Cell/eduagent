"""作业批改比对：OCR 结果与标准答案的规则化比对，产出坐标圈注数据。

边界说明：本模块只产出「结构化比对结果」（哪道题、识别答案、是否一致、
圈注坐标），不做学科归因与讲解——归因与讲解是调用方（Agent 编排服务）的职责。
"""

from __future__ import annotations

import re

from app.providers.base import OcrResult
from app.schemas import GradeItem, GradeMark, GradeResponse, OcrBox

# 与 document.QUESTION_PATTERN 同源的题号识别（OCR 文本逐行扫描）
QUESTION_PATTERN = re.compile(
    r"^\s*(?:第\s*(\d+)\s*题|(\d+)\s*[.、．]|\((\d+)\)|（(\d+)）)"
)

_MARK_COLORS = {"correct": "correct", "wrong": "wrong", "partial": "partial"}


def grade_from_ocr(
    ocr: OcrResult,
    subject: str,
    standard_answer: dict[str, str] | None,
) -> GradeResponse:
    """基于 OCR 结果逐题比对，生成带坐标圈注的结构化结果。"""
    items: list[GradeItem] = []
    for box in ocr.boxes:
        m = QUESTION_PATTERN.match(box["text"])
        if not m:
            continue
        number = next(g for g in m.groups() if g)
        extracted = QUESTION_PATTERN.sub("", box["text"], count=1).strip()

        item = GradeItem(
            question_box=OcrBox(**box),
            extracted_answer=extracted,
        )
        if standard_answer and number in standard_answer:
            correct = standard_answer[number]
            item.correct_answer = correct
            item.is_correct = compare_answers(extracted, correct, subject)
            item.marks = [
                _build_mark(box, item.is_correct, extracted, correct)
            ]
        items.append(item)
    return GradeResponse(subject=subject, items=items)


def compare_answers(extracted: str, standard: str, subject: str = "math") -> bool:
    """规则化比对：去空白/统一全半角后，数值等价或文本包含判对。

    NOTE: 此处为 LLM 回调预留接口——主观题/化简类等价判断的规则覆盖有限，
    生产环境可在比对失败时回调 Agent 编排服务的判分接口（由调用方注入），
    本服务不直接发起 LLM 调用，以保持「无学科业务逻辑」的边界。
    """
    norm_e = _normalize(extracted)
    norm_s = _normalize(standard)
    if not norm_e or not norm_s:
        return False
    if norm_e == norm_s:
        return True

    # 数值等价：提取识别结果末尾的数值与标准答案数值比较
    num_e = _extract_final_number(norm_e)
    num_s = _extract_final_number(norm_s)
    if num_e is not None and num_s is not None:
        return abs(num_e - num_s) < 1e-9

    # 文本包含（如选择题选项字母）
    return norm_s in norm_e or norm_e in norm_s


def _build_mark(box: dict, is_correct: bool, extracted: str, correct: str) -> GradeMark:
    """在题目框右侧生成圈注坐标（供前端 SVG 叠加渲染）。"""
    mark_type = _MARK_COLORS["correct"] if is_correct else _MARK_COLORS["wrong"]
    radius = max(box["h"], 24) // 2 + 8
    cx = box["x"] + box["w"] + radius + 6
    cy = box["y"] + box["h"] // 2
    comment = "答案正确" if is_correct else f"识别为「{extracted}」，正确答案「{correct}」"
    return GradeMark(type=mark_type, x=cx, y=cy, radius=radius, comment=comment)


def _normalize(text: str) -> str:
    """统一全半角、去空白与常见标点。"""
    trans = str.maketrans(
        "０１２３４５６７８９．，。：；？！（）　",
        "0123456789.,.:;?!() ",
    )
    text = text.translate(trans).lower()
    return re.sub(r"[\s,，。.;；:：]+$", "", re.sub(r"\s+", "", text))


def _extract_final_number(text: str) -> float | None:
    """取文本中最后一个数值（支持分数 a/b 与小数）。"""
    frac = re.findall(r"(-?\d+)\s*/\s*(\d+)", text)
    if frac:
        num, den = frac[-1]
        if int(den) != 0:
            return int(num) / int(den)
    nums = re.findall(r"-?\d+(?:\.\d+)?", text)
    return float(nums[-1]) if nums else None
