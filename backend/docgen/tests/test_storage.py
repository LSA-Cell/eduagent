"""Public URL signing keeps object writes on the internal MinIO endpoint."""

from types import SimpleNamespace

from app import storage as storage_module


def test_upload_uses_internal_client_and_get_uses_public_signing_host(monkeypatch):
    calls = []

    class Client:
        def __init__(self, endpoint, **kwargs):
            self.endpoint = endpoint
            calls.append(("client", endpoint, kwargs["secure"]))

        def bucket_exists(self, bucket):
            calls.append(("exists", self.endpoint, bucket))
            return True

        def put_object(self, bucket, key, data, **kwargs):
            calls.append(("put", self.endpoint, bucket, key, data.read()))

        def presigned_get_object(self, bucket, key, **kwargs):
            calls.append(("sign", self.endpoint, bucket, key))
            return f"https://{self.endpoint}/{bucket}/{key}?signature=test"

    settings = SimpleNamespace(
        minio_endpoint="minio:9000",
        minio_public_endpoint="files.example.com",
        minio_use_ssl=False,
        minio_public_use_ssl=True,
        minio_access_key="access",
        minio_secret_key="secret",
        minio_bucket_generated="generated",
    )
    monkeypatch.setattr(storage_module, "get_settings", lambda: settings)
    monkeypatch.setattr(storage_module, "Minio", Client)

    storage = storage_module.Storage()
    url, key = storage.upload_and_sign(b"example", "report.pdf", "application/pdf")
    assert key == "report.pdf"
    assert url.startswith("https://files.example.com/generated/report.pdf")
    assert ("put", "minio:9000", "generated", "report.pdf", b"example") in calls
    assert ("sign", "files.example.com", "generated", "report.pdf") in calls
