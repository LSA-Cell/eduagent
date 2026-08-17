"""MinIO 对象存储封装：知识库原始文件的上传预签名 / 下载 / 删除。

文件流不经过本服务：客户端凭预签名 URL 直传/直取 MinIO；bucket 取
settings.minio_bucket_uploads（默认私有，不设置任何公共访问策略）。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from minio import Minio

from eduagent_shared.config import Settings

DEFAULT_PRESIGNED_EXPIRES = 900  # 预签名 URL 默认有效期（秒）
MAX_PRESIGNED_EXPIRES = 3600  # 预签名 URL 有效期上限（秒）

_client: Optional[Minio] = None


def get_minio_client(settings: Settings) -> Minio:
    """进程级 MinIO 客户端（惰性创建，仅构造对象不建立连接）。"""
    global _client
    if _client is None:
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_use_ssl,
        )
    return _client


def ensure_bucket(client: Minio, bucket: str) -> None:
    """bucket 不存在则创建（MinIO 默认私有，不附加公共策略）。"""
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def create_presigned_put(
    object_key: str,
    *,
    settings: Settings,
    client: Optional[Minio] = None,
    expires: int = DEFAULT_PRESIGNED_EXPIRES,
) -> str:
    """生成上传预签名 URL（PUT），expires 不得超过 MAX_PRESIGNED_EXPIRES。"""
    if not 0 < expires <= MAX_PRESIGNED_EXPIRES:
        raise ValueError(f"expires 须在 1~{MAX_PRESIGNED_EXPIRES} 秒之间")
    client = client or get_minio_client(settings)
    ensure_bucket(client, settings.minio_bucket_uploads)
    return client.presigned_put_object(
        settings.minio_bucket_uploads, object_key, expires=timedelta(seconds=expires)
    )


def presigned_get(
    object_key: str,
    *,
    settings: Settings,
    client: Optional[Minio] = None,
    expires: int = DEFAULT_PRESIGNED_EXPIRES,
) -> str:
    """生成下载预签名 URL（GET），供外部服务（如多模态）取文件。"""
    if not 0 < expires <= MAX_PRESIGNED_EXPIRES:
        raise ValueError(f"expires 须在 1~{MAX_PRESIGNED_EXPIRES} 秒之间")
    client = client or get_minio_client(settings)
    ensure_bucket(client, settings.minio_bucket_uploads)
    return client.presigned_get_object(
        settings.minio_bucket_uploads, object_key, expires=timedelta(seconds=expires)
    )


def download_bytes(
    object_key: str, *, settings: Settings, client: Optional[Minio] = None
) -> bytes:
    """直接读取对象全部字节（任务端确认文件存在/取大小用）。"""
    client = client or get_minio_client(settings)
    response = client.get_object(settings.minio_bucket_uploads, object_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def remove_object(
    object_key: str, *, settings: Settings, client: Optional[Minio] = None
) -> None:
    """删除对象（S3 DELETE 幂等，对象不存在不报错）。"""
    client = client or get_minio_client(settings)
    client.remove_object(settings.minio_bucket_uploads, object_key)
