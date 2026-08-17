"""Denial Tracking 拒绝追踪（增强机制 §3.3）。

红线：计数只影响推荐排序与默认值，禁止用于限制用户主动发起的功能；
拒绝数据不出域、不共享（RLS 见 infra DDL，应用侧只写本人行）。
"""

from app.denial.counters import DenialTracker, load_counters, record_denial, record_use
from app.denial.models import DenialCounter
from app.denial.ranking import CONSECUTIVE_SINK_THRESHOLD, TOTAL_DEFAULT_OFF_THRESHOLD, apply_denial_ranking

__all__ = [
    "CONSECUTIVE_SINK_THRESHOLD",
    "DenialCounter",
    "DenialTracker",
    "TOTAL_DEFAULT_OFF_THRESHOLD",
    "apply_denial_ranking",
    "load_counters",
    "record_denial",
    "record_use",
]
