"""OCR / ASR / TTS 接口测试（mock provider）。"""

from __future__ import annotations

import io
import wave

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import TEST_HEADERS
from tests.helpers import make_image_bytes

client = TestClient(app, headers=TEST_HEADERS)


def test_ocr_mock_multipart() -> None:
    resp = client.post(
        "/ocr",
        files={"file": ("homework.jpg", make_image_bytes(), "image/jpeg")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["text"]
    assert isinstance(body["latex_blocks"], list) and len(body["latex_blocks"]) >= 1
    assert "\\frac" in body["latex_blocks"][0]
    box = body["boxes"][0]
    assert {"x", "y", "w", "h", "text"} <= set(box)


def test_ocr_mock_deterministic() -> None:
    img = make_image_bytes()
    r1 = client.post("/ocr", files={"file": ("a.jpg", img, "image/jpeg")})
    r2 = client.post("/ocr", files={"file": ("b.jpg", img, "image/jpeg")})
    assert r1.json() == r2.json()


def test_asr_mock() -> None:
    fake_audio = b"\x00" * 32000
    resp = client.post(
        "/asr",
        files={"file": ("voice.wav", fake_audio, "audio/wav")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["text"]
    assert body["language"] == "zh"
    assert len(body["segments"]) == 2
    seg = body["segments"][0]
    assert seg["start"] == 0.0 and seg["end"] > 0 and seg["text"]


def test_tts_mock_returns_wav() -> None:
    resp = client.post("/tts", json={"text": "同学们好", "voice": "default", "speed": 1.0})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(resp.content), "rb") as wf:
        assert wf.getframerate() == 16000
        assert wf.getnframes() > 0


def test_tts_speed_shortens_audio() -> None:
    slow = client.post("/tts", json={"text": "同学们好，今天我们学习分数", "speed": 1.0})
    fast = client.post("/tts", json={"text": "同学们好，今天我们学习分数", "speed": 2.0})
    assert len(fast.content) < len(slow.content)
