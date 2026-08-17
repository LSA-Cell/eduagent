"""测试公共工具：内存版 MinIO 替代实现。"""

from __future__ import annotations


class FakeStorage:
    """离线 MinIO 替身：记录上传内容，返回可预期的假 URL。"""

    def __init__(self) -> None:
        self.uploads: dict[str, bytes] = {}

    def upload_and_sign(self, data: bytes, object_key: str, content_type: str) -> tuple[str, str]:
        self.uploads[object_key] = data
        return f"http://minio.test/eduagent-generated/{object_key}?sig=fake", object_key

    def upload_bytes(self, data: bytes, object_key: str, content_type: str, bucket: str | None = None) -> str:
        self.uploads[object_key] = data
        return object_key

    def presigned_get(self, object_key: str, bucket: str | None = None) -> str:
        return f"http://minio.test/eduagent-generated/{object_key}?sig=fake"
