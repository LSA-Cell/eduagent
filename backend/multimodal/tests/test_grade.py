"""作业批改接口测试：结构化比对结果与圈注坐标结构。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import TEST_HEADERS
from tests.helpers import make_image_bytes

client = TestClient(app, headers=TEST_HEADERS)


def test_grade_homework_coordinate_structure() -> None:
    """mock OCR 产出 3 道题，比对标准答案后返回 marks 圈注坐标。"""
    answers = {"1": "8", "2": "5", "3": "12"}  # 第 2 题判错
    resp = client.post(
        "/grade-homework",
        files={"file": ("hw.jpg", make_image_bytes(), "image/jpeg")},
        data={"subject": "math", "standard_answer": json.dumps(answers, ensure_ascii=False)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subject"] == "math"
    assert len(body["items"]) == 3

    item1, item2, item3 = body["items"]
    assert item1["is_correct"] is True
    assert item2["is_correct"] is False
    assert item3["is_correct"] is True
    assert item2["correct_answer"] == "5"

    for item in body["items"]:
        box = item["question_box"]
        assert {"x", "y", "w", "h", "text"} <= set(box)
        assert len(item["marks"]) == 1
        mark = item["marks"][0]
        assert {"type", "x", "y", "radius", "comment"} <= set(mark)
        assert mark["type"] in {"correct", "wrong", "partial"}
        assert mark["radius"] > 0
        # 圈注在题目框右侧（SVG 叠加语义）
        assert mark["x"] > box["x"] + box["w"]
    assert item2["marks"][0]["type"] == "wrong"
    assert item1["marks"][0]["type"] == "correct"


def test_grade_homework_without_answers() -> None:
    """无标准答案时仅返回 OCR 结构化结果，is_correct 为 None。"""
    resp = client.post(
        "/grade-homework",
        files={"file": ("hw.jpg", make_image_bytes(), "image/jpeg")},
        data={"subject": "math"},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 3
    assert all(i["is_correct"] is None for i in items)
    assert all(i["marks"] == [] for i in items)
    assert items[0]["extracted_answer"]


def test_grade_homework_invalid_answer_json_400() -> None:
    resp = client.post(
        "/grade-homework",
        files={"file": ("hw.jpg", make_image_bytes(), "image/jpeg")},
        data={"standard_answer": "{not-json"},
    )
    assert resp.status_code == 400
