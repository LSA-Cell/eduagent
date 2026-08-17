"""配置：基于共享包 eduagent_shared.config.Settings，附加本服务特有字段。

红线：共享包为配置的唯一事实源（部署镜像经 `pip install ./shared` 保证可用，
见 Dockerfile），禁止"共享包未安装时的本地降级实现"——双配置源必然漂移。
本服务特有字段（Provider 选择）经继承扩展，字段名与环境变量约定不变。
"""

from __future__ import annotations

from functools import lru_cache

from eduagent_shared.config import Settings as _SharedSettings


class Settings(_SharedSettings):
    """多模态服务配置 = 共享全局配置 + Provider 选择。"""

    # Provider 选择：mock / paddleocr / whisper / edge
    ocr_provider: str = "mock"
    asr_provider: str = "mock"
    tts_provider: str = "mock"


@lru_cache
def get_settings() -> Settings:
    """获取进程级单例配置；测试中改环境变量后需 get_settings.cache_clear()。"""
    return Settings()
