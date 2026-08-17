"""多模态功能降级链声明与明示文案（§5.4.2）。

每个多模态功能必须在 FALLBACK_CHAINS 中声明降级链（红线：未声明不予上线）：
- 语音对练 voice_practice：voice → text
- 拍照搜题 photo_search：camera → album_upload → manual_input
- OCR 批改 ocr_grading：ocr → manual_select

服务故障时 resolve_fallback 返回结构化切换结果，notice 为必须展示给用户的
明示文案（红线：禁止静默降级）。运营调整链路时经 chains 覆盖参数注入，
禁止改代码散写 if env。
"""

from __future__ import annotations

from typing import Any, Optional

# 功能 → 模式降级链（有序，前者为默认模式）
FALLBACK_CHAINS: dict[str, list[str]] = {
    "voice_practice": ["voice", "text"],
    "photo_search": ["camera", "album_upload", "manual_input"],
    "ocr_grading": ["ocr", "manual_select"],
}

_MODE_LABELS = {
    "voice": "语音模式",
    "text": "文本模式",
    "camera": "拍照模式",
    "album_upload": "相册上传",
    "manual_input": "手动输入题干",
    "ocr": "OCR 批改",
    "manual_select": "手动框选",
}

_MODE_SERVICE = {
    "voice": "语音服务",
    "camera": "拍照服务",
    "album_upload": "相册服务",
    "ocr": "OCR 服务",
}


def has_fallback_chain(feature: str, *, chains: Optional[dict[str, list[str]]] = None) -> bool:
    """该多模态功能是否已声明降级链（上线前检查口径）。"""
    chain = (chains or FALLBACK_CHAINS).get(feature) or []
    return len(chain) >= 2


def next_mode(
    feature: str,
    current_mode: str,
    *,
    chains: Optional[dict[str, list[str]]] = None,
) -> Optional[str]:
    """降级链上的下一个模式；已到链尾或功能未声明返回 None。"""
    chain = (chains or FALLBACK_CHAINS).get(feature) or []
    try:
        index = chain.index(current_mode)
    except ValueError:
        return None
    if index + 1 >= len(chain):
        return None
    return chain[index + 1]


def resolve_fallback(
    feature: str,
    failed_mode: str,
    *,
    chains: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    """服务故障 → 返回结构化降级结果（notice 必须展示给用户，禁止静默）。

    返回 {"feature", "from_mode", "to_mode", "notice", "degraded"}：
    - degraded=True：已切换到低级模式，notice 形如"语音服务暂不可用，已切换文本模式"；
    - degraded=False：链尾无更低模式，notice 明示服务不可用并请稍后再试。
    """
    target = next_mode(feature, failed_mode, chains=chains)
    service = _MODE_SERVICE.get(failed_mode, "当前服务")
    if target is not None:
        return {
            "feature": feature,
            "from_mode": failed_mode,
            "to_mode": target,
            "degraded": True,
            "notice": f"{service}暂不可用，已切换{_MODE_LABELS.get(target, target)}",
        }
    return {
        "feature": feature,
        "from_mode": failed_mode,
        "to_mode": None,
        "degraded": False,
        "notice": f"{service}暂不可用，请稍后再试。",
    }


__all__ = ["FALLBACK_CHAINS", "has_fallback_chain", "next_mode", "resolve_fallback"]
