"""内部令牌鉴权测试（覆盖共享包/本地降级两条路径，行为一致）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import get_settings
from tests.helpers import make_image_bytes


def test_internal_token_enforced(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "test-secret")
    get_settings.cache_clear()
    try:
        from app.main import app

        client = TestClient(app)
        # 健康检查不受影响
        assert client.get("/healthz").status_code == 200
        # 缺令牌 -> 401
        resp = client.post(
            "/ocr", files={"file": ("a.jpg", make_image_bytes(), "image/jpeg")}
        )
        assert resp.status_code == 401
        # 错令牌 -> 401
        resp = client.post(
            "/ocr",
            files={"file": ("a.jpg", make_image_bytes(), "image/jpeg")},
            headers={"X-Internal-Token": "wrong"},
        )
        assert resp.status_code == 401
        # 正确令牌 -> 200
        resp = client.post(
            "/ocr",
            files={"file": ("a.jpg", make_image_bytes(), "image/jpeg")},
            headers={"X-Internal-Token": "test-secret"},
        )
        assert resp.status_code == 200
    finally:
        get_settings.cache_clear()


def test_healthz() -> None:
    from app.main import app

    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
