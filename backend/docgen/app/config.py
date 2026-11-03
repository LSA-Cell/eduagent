"""配置：基于共享包 eduagent_shared.config.Settings。

共享包（backend/shared，pip 本地包）由同事开发；离线开发阶段若尚未安装，
降级为本地等价实现，字段名与共享包保持一致（小写，环境变量大小写不敏感）。
"""

from __future__ import annotations

from functools import lru_cache

try:  # pragma: no cover - 取决于运行环境是否安装了共享包
    from eduagent_shared.config import Settings  # noqa: F401

except ImportError:  # 共享包未安装时的本地降级实现
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class Settings(BaseSettings):  # type: ignore[no-redef]
        """与 eduagent_shared.config.Settings 字段对齐的本地配置。"""

        model_config = SettingsConfigDict(
            env_file=".env", extra="ignore", case_sensitive=False
        )

        app_env: str = "dev"
        log_level: str = "INFO"

        database_url: str = ""
        redis_url: str = ""

        minio_endpoint: str = "localhost:9000"
        minio_public_endpoint: str = "localhost:9000"
        minio_public_use_ssl: bool = False
        minio_access_key: str = ""
        minio_secret_key: str = ""
        minio_bucket_uploads: str = "eduagent-uploads"
        minio_bucket_generated: str = "eduagent-generated"
        minio_bucket_templates: str = "eduagent-templates"
        minio_use_ssl: bool = False

        internal_service_token: str = ""


@lru_cache
def get_settings() -> Settings:
    """获取进程级单例配置；测试中改环境变量后需 get_settings.cache_clear()。"""
    return Settings()
