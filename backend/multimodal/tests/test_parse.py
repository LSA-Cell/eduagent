"""文档解析接口测试：pdf / docx / txt 与试卷分题。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import TEST_HEADERS
from tests.helpers import make_docx_bytes, make_pdf_bytes

client = TestClient(app, headers=TEST_HEADERS)


def test_parse_pdf() -> None:
    pdf = make_pdf_bytes(["Chapter 1 Numbers", "1. What is 3+5?", "2. What is 10/2?"])
    resp = client.post(
        "/parse-document",
        files={"file": ("quiz.pdf", pdf, "application/pdf")},
        data={"auto_split_questions": "true"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"]
    assert len(body["sections"]) >= 1
    assert body["sections"][0]["page"] == 1
    numbers = [q["number"] for q in body["questions"]]
    assert numbers == ["1", "2"]


def test_parse_docx_with_question_split() -> None:
    docx_bytes = make_docx_bytes(
        "三年级数学试卷",
        [
            ("heading1", "一、计算题"),
            ("para", "1. 3+5=?"),
            ("para", "2. 7x=21, x=?"),
            ("heading1", "二、应用题"),
            ("para", "3. 小明有 12 个苹果，吃了 3 个，还剩几个？"),
        ],
    )
    resp = client.post(
        "/parse-document",
        files={"file": ("paper.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"auto_split_questions": "true"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "三年级数学试卷"
    headings = [s["heading"] for s in body["sections"]]
    assert "一、计算题" in headings and "二、应用题" in headings
    assert [q["number"] for q in body["questions"]] == ["1", "2", "3"]
    assert "3+5" in body["questions"][0]["text"]


def test_parse_docx_without_split() -> None:
    docx_bytes = make_docx_bytes("教案", [("para", "教学目标：掌握分数加法。")])
    resp = client.post(
        "/parse-document",
        files={"file": ("plan.docx", docx_bytes, "application/octet-stream")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["questions"] == []


def test_parse_txt() -> None:
    resp = client.post(
        "/parse-document",
        files={"file": ("note.txt", "学习笔记\n分数加法通分。".encode("utf-8"), "text/plain")},
        data={"file_type": "txt"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "学习笔记"


def test_parse_unsupported_type_400() -> None:
    resp = client.post(
        "/parse-document",
        files={"file": ("a.exe", b"MZ", "application/octet-stream")},
        data={"file_type": "exe"},
    )
    assert resp.status_code == 400


def test_parse_missing_input_400() -> None:
    resp = client.post("/parse-document", data={"file_type": "pdf"})
    assert resp.status_code == 400
