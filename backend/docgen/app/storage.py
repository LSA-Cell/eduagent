"""MinIO 封装：bucket 自动创建（私有）、字节上传、预签名 URL（≤3600s）。"""

from __future__ import annotations

import io
from datetime import timedelta

from minio import Minio

from app.config import get_settings

PRESIGNED_EXPIRES_SECONDS = 3600  # 预签名 URL 过期时间 1h


class Storage:
    """MinIO 客户端薄封装。bucket 在首次上传时确保存在。"""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_use_ssl,
        )
        public_endpoint = getattr(settings, "minio_public_endpoint", None)
        self._signing_client = self._client
        if public_endpoint and public_endpoint != settings.minio_endpoint:
            # SigV4 signs Host; generate client-facing URLs against the public host.
            self._signing_client = Minio(
                public_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=getattr(settings, "minio_public_use_ssl", settings.minio_use_ssl),
                region="us-east-1",
            )
        self.default_bucket = settings.minio_bucket_generated

    def ensure_bucket(self, bucket: str) -> None:
        """bucket 不存在则创建（MinIO 默认私有，不设公开策略）。"""
        if not self._client.bucket_exists(bucket):
            self._client.make_bucket(bucket)

    def upload_bytes(
        self,
        data: bytes,
        object_key: str,
        content_type: str,
        bucket: str | None = None,
    ) -> str:
        """上传字节流，返回对象 key。"""
        bucket = bucket or self.default_bucket
        self.ensure_bucket(bucket)
        self._client.put_object(
            bucket,
            object_key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        return object_key

    def presigned_get(self, object_key: str, bucket: str | None = None) -> str:
        """生成 GET 预签名 URL，过期 1h。"""
        bucket = bucket or self.default_bucket
        return self._signing_client.presigned_get_object(
            bucket, object_key, expires=timedelta(seconds=PRESIGNED_EXPIRES_SECONDS)
        )

    def upload_and_sign(
        self, data: bytes, object_key: str, content_type: str
    ) -> tuple[str, str]:
        """上传并返回 (file_url, object_key) 便捷组合。"""
        key = self.upload_bytes(data, object_key, content_type)
        return self.presigned_get(key), key


_storage: Storage | None = None


def get_storage() -> Storage:
    """进程级单例；测试中可 monkeypatch 本函数替换为内存实现。"""
    global _storage
    if _storage is None:
        _storage = Storage()
    return _storage
