"""冒烟脚本回归：真实 HTTP 另由 smoke_integration 在隔离环境执行。"""

from __future__ import annotations

import io
import unittest
import urllib.error
from unittest.mock import patch

import smoke_integration as smoke


class SmokeIntegrationTests(unittest.TestCase):
    def test_signed_download_requires_signature_and_maximum_one_hour(self):
        base = "http://minio:9000/generated/test.docx?X-Amz-Signature=private-signature"
        self.assertTrue(smoke.valid_presigned_url(base + "&X-Amz-Expires=3600"))
        for value in (None, "http://minio/file", base + "&X-Amz-Expires=3601",
                      base + "&X-Amz-Expires=invalid", "http://["):
            with self.subTest(value=value):
                self.assertFalse(smoke.valid_presigned_url(value))

    def test_connection_failures_are_reported_instead_of_crashing(self):
        with patch.object(smoke.urllib.request, "urlopen", side_effect=urllib.error.URLError("down")):
            self.assertEqual(smoke.req("GET", "http://localhost/health")[0], 0)
            self.assertEqual(smoke.req_multipart("http://localhost/ocr", "file", "x.png", b"x")[0], 0)

    def run_script(self, chat_code=502, include_dev_code=True, argv=None):
        calls = []
        registration = {}
        signed = "http://minio:9000/generated/test.docx?X-Amz-Signature=private-signature&X-Amz-Expires=3600"

        def fake_req(method, url, body=None, headers=None, timeout=15):
            calls.append((method, url, body, headers))
            if url.endswith("/health"):
                return 200, {"status": "ok"}
            if url.endswith("/auth/sms-code"):
                return 200, {"dev_code": "123456"} if include_dev_code else {}
            if url.endswith("/auth/register"):
                self.assertEqual(body["sms_code"], "123456")
                registration.update(body)
                return 201, {"tokens": {"access_token": "private-access", "refresh_token": "private-refresh"}}
            if url.endswith(("/auth/login", "/auth/refresh")):
                return 200, {"access_token": "private-access"}
            if url.endswith("/users/me"):
                return 200, {"phone": registration["phone"]}
            if url.endswith("/chat/sessions"):
                return (201, {"id": "session"}) if method == "POST" else (200, {"items": []})
            if url.endswith("/messages"):
                return 200, {"items": []}
            if url.endswith("/v1/models"):
                self.assertEqual(headers["X-Internal-Token"], "private-internal")
                return 200, {"data": [{"id": "edu-general-14b"}]}
            if url.endswith("/render/lesson_plan") and headers and headers.get("X-Internal-Token") == "private-internal":
                return 202, {"task_id": "render-task"}
            if url.endswith("/tasks/render-task"):
                return 200, {"status": "completed", "result": {"file_url": signed}}
            if ":8000/" in url and url.endswith("/chat/completions"):
                self.assertEqual(body["inference_source"], "internal")
                self.assertFalse(body["auto_failover"])
                return chat_code, {"error": {"message": "unavailable"}}
            return (403 if ":8001/" in url else 401), {"error": "unauthorized"}

        output = io.StringIO()
        with patch.dict(smoke.os.environ, {"INTERNAL_SERVICE_TOKEN": "private-internal"}), \
             patch.object(smoke, "req", side_effect=fake_req), \
             patch.object(smoke, "req_multipart", return_value=(200, {"text": "ok"})), \
             patch.object(smoke.time, "sleep"), \
             patch("sys.stdout", output):
            status = smoke.main(argv or [])
        return status, calls, output.getvalue()

    def test_current_auth_and_nested_docgen_contracts_and_billing_health(self):
        status, calls, output = self.run_script()
        self.assertEqual(status, 0, output)
        self.assertTrue(any(url.endswith(":8006/health") for _, url, _, _ in calls))
        self.assertIn("auth-negative/billing/wrong", output)
        self.assertIn("docgen/render+minio", output)
        for secret in ("private-access", "private-refresh", "private-internal", "private-signature", "123456"):
            self.assertNotIn(secret, output)

    def test_missing_dev_code_stops_registration(self):
        status, calls, _ = self.run_script(include_dev_code=False)
        self.assertEqual(status, 1)
        self.assertFalse(any(url.endswith("/auth/register") for _, url, _, _ in calls))

    def test_compose_dns_mode_uses_existing_service_names(self):
        status, calls, output = self.run_script(argv=["--docker-network"])
        self.assertEqual(status, 0, output)
        self.assertTrue(any(url.startswith("http://agent:8002/") for _, url, _, _ in calls))

    def test_missing_chat_route_is_a_failure(self):
        status, _, _ = self.run_script(chat_code=404)
        self.assertEqual(status, 1)

    def test_missing_internal_token_is_rejected_before_network(self):
        with patch.dict(smoke.os.environ, {}, clear=True), patch.object(smoke, "req") as req, \
             patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as exc:
            smoke.main([])
        self.assertEqual(exc.exception.code, 2)
        req.assert_not_called()


if __name__ == "__main__":
    unittest.main()
