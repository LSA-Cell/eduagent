"""拒绝计数读写：record_denial / record_use（fail-soft 封装见 DenialTracker）。

- record_denial：consecutive+1、total+1；
- record_use：用户主动使用一次 → consecutive 清零（total 为累计口径，不回退）。

所有函数接收注入的 DB 会话（鸭子类型），自身不抛 I/O 语义以外的异常；
DenialTracker 再包一层 deps.session_factory + fail-soft。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select

from app.denial.models import DenialCounter

logger = logging.getLogger(__name__)


def _as_uuid(user_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(user_id))
    except (ValueError, AttributeError):
        # 测试/降级场景的非 UUID 用户标识：确定性映射，避免写库失败
        return uuid.uuid5(uuid.NAMESPACE_URL, f"eduagent:user:{user_id}")


async def _find(session: Any, user_id: str, target_code: str) -> Optional[DenialCounter]:
    """按 (user_id, target_code) 查找（DB 侧过滤 + Python 侧复核，兼容假会话）。"""
    uid = _as_uuid(user_id)
    result = await session.execute(
        select(DenialCounter).where(
            DenialCounter.user_id == uid, DenialCounter.target_code == target_code
        )
    )
    for row in result.scalars().all():
        if str(row.user_id) == str(uid) and row.target_code == target_code:
            return row
    return None


async def record_denial(session: Any, user_id: str, target_code: str) -> DenialCounter:
    """记录一次拒绝：consecutive+1、total+1（不存在则建行）。"""
    counter = await _find(session, user_id, target_code)
    if counter is None:
        counter = DenialCounter(user_id=_as_uuid(user_id), target_code=target_code)
        session.add(counter)
    counter.consecutive = int(counter.consecutive or 0) + 1
    counter.total = int(counter.total or 0) + 1
    await session.flush()
    return counter


async def record_use(session: Any, user_id: str, target_code: str) -> Optional[DenialCounter]:
    """用户主动使用一次：consecutive 清零（total 不回退）。无记录时为无操作。"""
    counter = await _find(session, user_id, target_code)
    if counter is None:
        return None
    counter.consecutive = 0
    await session.flush()
    return counter


async def load_counters(session: Any, user_id: str) -> dict[str, DenialCounter]:
    """加载该用户全部计数（target_code → counter），排序/默认值判定用。"""
    uid = _as_uuid(user_id)
    result = await session.execute(
        select(DenialCounter).where(DenialCounter.user_id == uid)
    )
    return {
        row.target_code: row
        for row in result.scalars().all()
        if str(row.user_id) == str(uid)
    }


class DenialTracker:
    """deps 级 fail-soft 封装：任何 DB 失败仅记日志，绝不影响主流程。"""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def denial(self, user_id: str, target_code: str) -> None:
        try:
            async with self._session_factory() as session:
                await record_denial(session, user_id, target_code)
                await session.commit()
        except Exception:  # noqa: BLE001 - 计数失败不阻断请求
            logger.warning("拒绝计数写入失败（%s）", target_code, exc_info=True)

    async def use(self, user_id: str, target_code: str) -> None:
        try:
            async with self._session_factory() as session:
                await record_use(session, user_id, target_code)
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.warning("拒绝计数清零失败（%s）", target_code, exc_info=True)

    async def counters(self, user_id: str) -> dict[str, DenialCounter]:
        try:
            async with self._session_factory() as session:
                return await load_counters(session, user_id)
        except Exception:  # noqa: BLE001
            logger.warning("拒绝计数读取失败，降级为无计数", exc_info=True)
            return {}
