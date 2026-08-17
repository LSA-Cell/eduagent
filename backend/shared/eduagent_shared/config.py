"""全局配置：字段与根目录 .env.example 完全对齐。

所有服务共享同一份 Settings；各服务按需取用字段。
环境变量大小写不敏感，可直接读取 .env 文件。
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """EduAgent 全局配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 全局 ----------
    app_env: str = "dev"
    log_level: str = "INFO"

    # ---------- PostgreSQL / Redis / MinIO ----------
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "eduagent"
    postgres_user: str = "eduagent"
    postgres_password: str = "change_me_postgres"
    database_url: str = "postgresql+asyncpg://eduagent:change_me_postgres@postgres:5432/eduagent"

    redis_host: str = "redis"
    redis_port: int = 6379
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "eduagent_minio"
    minio_secret_key: str = "change_me_minio"
    minio_bucket_uploads: str = "eduagent-uploads"
    minio_bucket_generated: str = "eduagent-generated"
    minio_bucket_templates: str = "eduagent-templates"
    minio_use_ssl: bool = False

    # ---------- 安全 ----------
    jwt_secret: str = "change_me_jwt_secret"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    # AES-256-GCM 主密钥（标准值为 32 字节 base64；开发期占位字符串会被 SHA-256 派生，仅用于本地）
    key_encryption_master_key: str = "change_me_base64_32bytes_master_key"
    # 轮换期旧主密钥（可选）：解密时新钥失败会回退尝试旧钥，见 crypto.py 轮换说明
    key_encryption_master_key_previous: str | None = None

    # ---------- 服务间地址 ----------
    bff_base_url: str = "http://bff:8000"
    inference_gateway_url: str = "http://gateway:8001"
    agent_orchestrator_url: str = "http://agent:8002"
    multimodal_url: str = "http://multimodal:8003"
    api_proxy_url: str = "http://proxy:8004"
    docgen_url: str = "http://docgen:8005"
    internal_service_token: str = "change_me_internal_service_token"

    # ---------- 推理网关 ----------
    # 自有模型路由表（JSON 字符串）：model 名 -> vLLM 实例 URL 列表
    vllm_routes: dict[str, list[str]] = {}
    model_health_ttl_seconds: int = 30
    failover_enabled: bool = True

    # ---------- 第三方搜索 ----------
    web_search_provider: str = "serper"
    web_search_api_key: str = ""

    # ---------- 限流（RPM） ----------
    rate_limit_free: int = 20
    rate_limit_paid: int = 60
    rate_limit_teacher: int = 100

    @field_validator("vllm_routes", mode="before")
    @classmethod
    def _parse_vllm_routes(cls, v: Any) -> Any:
        """支持 JSON 字符串形式（.env 中 VLLM_ROUTES）。"""
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return {}
            return json.loads(v)
        return v


@lru_cache
def get_settings() -> Settings:
    """进程级缓存的配置单例；测试中改环境变量后需调用 get_settings.cache_clear()。"""
    return Settings()
