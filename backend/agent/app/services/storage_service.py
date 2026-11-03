"""MinIO 对象存储封装：知识库原始文件的上传预签名 / 下载 / 删除。

文件流不经过本服务：客户端凭预签名 URL 直传/直取 MinIO；bucket 取
settings.minio_bucket_uploads（默认私有，不设置任何公共访问策略）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from minio import Minio
from minio.datatypes import PostPolicy

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


def _public_signing_client(settings: Settings, internal_client: Minio, injected: bool) -> Minio:
    """SigV4 包含 Host；公开 URL 必须由公开地址对应的客户端签发。"""
    public_endpoint = getattr(settings, "minio_public_endpoint", None)
    if injected or not public_endpoint or public_endpoint == settings.minio_endpoint:
        return internal_client
    return Minio(
        public_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=getattr(settings, "minio_public_use_ssl", settings.minio_use_ssl),
        region="us-east-1",
    )


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
    internal_client = client or get_minio_client(settings)
    ensure_bucket(internal_client, settings.minio_bucket_uploads)
    signing_client = _public_signing_client(settings, internal_client, client is not None)
    return signing_client.presigned_put_object(
        settings.minio_bucket_uploads, object_key, expires=timedelta(seconds=expires)
    )


def create_presigned_post(
    object_key: str,
    content_type: str,
    *,
    settings: Settings,
    max_size: int,
    client: Optional[Minio] = None,
    expires: int = DEFAULT_PRESIGNED_EXPIRES,
) -> tuple[str, dict[str, str]]:
    """签发固定对象键、媒体类型和文件大小的浏览器表单上传策略。"""
    if not 0 < expires <= MAX_PRESIGNED_EXPIRES:
        raise ValueError(f"expires 须在 1~{MAX_PRESIGNED_EXPIRES} 秒之间")
    if max_size < 1:
        raise ValueError("max_size must be positive")
    signer = client or get_minio_client(settings)
    ensure_bucket(signer, settings.minio_bucket_uploads)
    policy = PostPolicy(
        settings.minio_bucket_uploads,
        datetime.now(timezone.utc) + timedelta(seconds=expires),
    )
    policy.add_equals_condition("key", object_key)
    policy.add_equals_condition("Content-Type", content_type)
    policy.add_content_length_range_condition(1, max_size)
    fields = signer.presigned_post_policy(policy)
    fields.update({"key": object_key, "Content-Type": content_type})
    endpoint = getattr(settings, "minio_public_endpoint", settings.minio_endpoint)
    secure = getattr(settings, "minio_public_use_ssl", settings.minio_use_ssl)
    upload_url = f"{'https' if secure else 'http'}://{endpoint}/{settings.minio_bucket_uploads}"
    return upload_url, fields


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
    internal_client = client or get_minio_client(settings)
    ensure_bucket(internal_client, settings.minio_bucket_uploads)
    signing_client = _public_signing_client(settings, internal_client, client is not None)
    return signing_client.presigned_get_object(
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


def stat_object(
    object_key: str, *, settings: Settings, client: Optional[Minio] = None
):
    """读取对象大小及媒体类型；对象不存在时保留 S3 错误供路由转换。"""
    client = client or get_minio_client(settings)
    return client.stat_object(settings.minio_bucket_uploads, object_key)


def remove_object(
    object_key: str, *, settings: Settings, client: Optional[Minio] = None
) -> None:
    """删除对象（S3 DELETE 幂等，对象不存在不报错）。"""
    client = client or get_minio_client(settings)
    client.remove_object(settings.minio_bucket_uploads, object_key)
