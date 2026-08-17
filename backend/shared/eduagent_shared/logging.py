"""structlog 日志配置，内置敏感字段脱敏 filter。

脱敏规则：
- 字段名包含 api_key / authorization / password / secret / token 的值一律替换为 ``***``；
- 字符串值中的 ``Bearer xxx`` 令牌片段替换为 ``Bearer ***``。
"""

from __future__ import annotations

import logging
import re
from typing import Any

import structlog

from .config import get_settings

_SENSITIVE_PARTS = ("api_key", "apikey", "authorization", "password", "secret", "token")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*")
MASK = "***"


def mask_sensitive_processor(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor：对敏感字段与 Bearer 令牌自动脱敏。"""
    for key, value in list(event_dict.items()):
        lowered = key.lower()
        if any(part in lowered for part in _SENSITIVE_PARTS):
            event_dict[key] = MASK
        elif isinstance(value, str) and "bearer" in value.lower():
            event_dict[key] = _BEARER_RE.sub(r"\1" + MASK, value)
    return event_dict


def configure_logging(level: str | None = None, *, json_logs: bool | None = None) -> None:
    """初始化 structlog；dev 环境输出彩色控制台格式，其余输出 JSON。"""
    settings = get_settings()
    level = (level or settings.log_level).upper()
    if json_logs is None:
        json_logs = settings.app_env != "dev"

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            mask_sensitive_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level, logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """取一个绑定了模块名的 logger。

    注意：PrintLoggerFactory 不带 name 属性，structlog 的 add_logger_name
    processor 在该组合下不可用（且 structlog ≥25 已将其移除），因此模块名
    通过 bind 到 ``logger`` 字段呈现（get_logger(logger=...) 与 wrap_logger
    形参冲突，不可用）。
    """
    return structlog.get_logger().bind(logger=name) if name else structlog.get_logger()
