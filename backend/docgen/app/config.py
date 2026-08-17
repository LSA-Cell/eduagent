"""配置：统一使用共享包 eduagent_shared.config.Settings。

红线：共享包为配置的唯一事实源（部署镜像经 `pip install ./shared` 保证可用，
见 Dockerfile），禁止"共享包未安装时的本地降级实现"——双配置源必然漂移。
"""

from __future__ import annotations

from functools import lru_cache

from eduagent_shared.config import Settings  # noqa: F401


@lru_cache
def get_settings() -> Settings:
    """获取进程级单例配置；测试中改环境变量后需 get_settings.cache_clear()。"""
    return Settings()
