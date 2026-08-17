"""output_styles 注册表（§5.3）：表读取 + 进程内缓存 60s 热生效。

查找顺序（确定性，禁止逐字变动——缓存红线 §3.1）：
精确 (grade_level, subject) → (grade_level, '*') → ('*', subject) → ('*', '*')。

命中结果注入 System Prompt 固定区块 <output_style>（前缀稳定区的一部分：
同一注册表缓存内容下输出字节一致；缓存 60s 滚动刷新属运营热生效语义，
单 run 内不变）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from sqlalchemy import select

from app.styles.models import OutputStyle

logger = logging.getLogger(__name__)

CACHE_TTL_S = 60.0
STYLE_BLOCK_OPEN = "<output_style>"
STYLE_BLOCK_CLOSE = "</output_style>"


class StyleRegistry:
    """output_styles 表读取 + 进程内缓存（未命中/DB 故障 → 不注入风格）。"""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        cache_ttl_s: float = CACHE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._cache_ttl_s = cache_ttl_s
        self._clock = clock
        self._cache: dict[tuple[str, str], str] = {}
        self._loaded_at: Optional[float] = None

    @property
    def stale(self) -> bool:
        if self._loaded_at is None:
            return True
        return (self._clock() - self._loaded_at) > self._cache_ttl_s

    async def refresh(self) -> None:
        """从 DB 重载缓存；异常保留旧缓存（无旧缓存=不注入）。"""
        try:
            async with self._session_factory() as session:
                rows = (
                    await session.execute(
                        select(OutputStyle).where(OutputStyle.is_active.is_(True))
                    )
                ).scalars().all()
            cache: dict[tuple[str, str], str] = {}
            for row in rows:
                if not getattr(row, "is_active", True):  # Python 侧复核
                    continue
                tone = str(getattr(row, "tone_prompt", "") or "").strip()
                if not tone:
                    continue
                key = (
                    str(getattr(row, "grade_level", "") or "*"),
                    str(getattr(row, "subject", "") or "*"),
                )
                cache[key] = tone
            self._cache = cache
            self._loaded_at = self._clock()
        except Exception:  # noqa: BLE001 - DB 故障：保留旧缓存
            logger.warning("output_styles 加载失败，沿用旧缓存", exc_info=True)

    def tone_prompt(
        self, grade_level: Optional[str], subject: Optional[str]
    ) -> Optional[str]:
        """同步查找（纯缓存，无 I/O）；未命中返回 None（不注入风格区块）。"""
        if self.stale:
            return None
        grade = str(grade_level) if grade_level else "*"
        subj = str(subject) if subject else "*"
        for key in ((grade, subj), (grade, "*"), ("*", subj), ("*", "*")):
            tone = self._cache.get(key)
            if tone:
                return tone
        return None


def format_style_block(tone_prompt: str) -> str:
    """把语气提示词包装成 <output_style> 固定区块（纯函数，确定性）。"""
    return "\n".join([
        STYLE_BLOCK_OPEN,
        str(tone_prompt).strip(),
        "（以上仅影响表述方式；事实正确性与内容审核标准不受影响。）",
        STYLE_BLOCK_CLOSE,
    ])


__all__ = [
    "CACHE_TTL_S",
    "STYLE_BLOCK_CLOSE",
    "STYLE_BLOCK_OPEN",
    "StyleRegistry",
    "format_style_block",
]
