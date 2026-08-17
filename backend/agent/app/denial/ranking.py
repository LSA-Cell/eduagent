"""推荐排序降频（§3.3）：纯函数，只输出排序/默认值建议，不做任何拦截。

阈值策略（规格固定）：
- consecutive ≥3 → 推荐降频（排序沉底，相对顺序稳定）；
- total ≥20 → 默认关闭标记（default_enabled=False，下次启动时提示由前端处理）。

红线：计数只影响推荐排序与默认值——本模块只重排/加注标记，
禁止删除条目或阻止用户主动发起。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

CONSECUTIVE_SINK_THRESHOLD = 3
TOTAL_DEFAULT_OFF_THRESHOLD = 20


def _counter_value(counter: Any, field: str) -> int:
    """counter 兼容 ORM 对象 / dict / (consecutive, total) 元组。"""
    if counter is None:
        return 0
    if isinstance(counter, (tuple, list)):
        return int(counter[0 if field == "consecutive" else 1] or 0)
    if isinstance(counter, Mapping):
        return int(counter.get(field, 0) or 0)
    return int(getattr(counter, field, 0) or 0)


def _item_code(item: Any) -> str:
    if isinstance(item, Mapping):
        return str(item.get("code") or item.get("target_code") or "")
    return str(getattr(item, "code", "") or getattr(item, "target_code", "") or "")


def apply_denial_ranking(
    items: Sequence[Any],
    counters: Mapping[str, Any],
    *,
    consecutive_threshold: int = CONSECUTIVE_SINK_THRESHOLD,
    total_threshold: int = TOTAL_DEFAULT_OFF_THRESHOLD,
) -> list[Any]:
    """按拒绝计数重排/标注推荐项，返回新列表（原列表与条目不被修改）。

    - consecutive ≥ 阈值的条目沉底（稳定排序，同组保持原相对顺序）；
    - total ≥ 阈值的条目加 default_enabled=False 标记（dict 条目加键，
      对象条目设置同名属性）；条目始终保留在结果中，禁止借此拦截。
    """
    annotated: list[tuple[bool, int, Any]] = []  # (沉底?, 原下标, 条目)
    for index, item in enumerate(items):
        counter = counters.get(_item_code(item))
        consecutive = _counter_value(counter, "consecutive")
        total = _counter_value(counter, "total")
        default_enabled = total < total_threshold
        if isinstance(item, Mapping):
            item = {
                **item,
                "denial_consecutive": consecutive,
                "denial_total": total,
                "default_enabled": default_enabled,
            }
        else:
            try:
                setattr(item, "denial_consecutive", consecutive)
                setattr(item, "denial_total", total)
                setattr(item, "default_enabled", default_enabled)
            except (AttributeError, TypeError):
                pass  # 不可标注的对象：仅参与排序
        annotated.append((consecutive >= consecutive_threshold, index, item))
    # 稳定排序：未沉底组在前，沉底组在后，各组内保持原顺序
    annotated.sort(key=lambda entry: (entry[0], entry[1]))
    return [item for _, _, item in annotated]


def should_default_off(counter: Any, *, total_threshold: int = TOTAL_DEFAULT_OFF_THRESHOLD) -> bool:
    """total ≥20 → 默认关闭建议（仅默认值，不拦截主动发起）。"""
    return _counter_value(counter, "total") >= total_threshold
