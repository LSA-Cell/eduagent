"""口语对练评分路由：LLM 按 JSON 输出四维评分 + 总评，解析失败有兜底。

轻量实现：不做音频转写（ASR 后续由多模态服务接入），入参为场景与转写文本。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app import deps
from app.prompts import VOICE_EVAL_PROMPT
from app.schemas import VoiceEvaluateRequest
from app.services.llm_client import LLMClient

router = APIRouter(
    prefix="/voice",
    tags=["voice"],
    dependencies=[Depends(deps.require_internal_auth)],
)

_DIMS = ("pronunciation", "grammar", "vocabulary", "fluency", "overall")

_FALLBACK_FEEDBACK = "评分服务暂不可用，请稍后重试"


def _clamp_score(value: Any) -> float:
    """LLM 输出容错：非数字按 0 处理，钳制到 0~100。"""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, score))


@router.post("/evaluate")
async def evaluate_voice(
    req: VoiceEvaluateRequest,
    llm: LLMClient = Depends(deps.get_llm),
) -> dict[str, Any]:
    """四维评分（发音/语法/用词/流利度）+ 综合分 + 中文总评。"""
    try:
        raw = await llm.chat_json(
            VOICE_EVAL_PROMPT.format(scenario=req.scenario),
            req.transcript or "（无转写文本）",
        )
        if not isinstance(raw, dict):
            raise ValueError(f"评分输出非 JSON 对象: {str(raw)[:100]}")
        scores = {dim: _clamp_score(raw.get(dim)) for dim in _DIMS}
        feedback = str(raw.get("feedback") or "")
    except Exception:  # noqa: BLE001 - LLM 网关异常/输出非法时兜底，不 5xx
        scores = {dim: 0.0 for dim in _DIMS}
        feedback = _FALLBACK_FEEDBACK
    return {**scores, "scenario": req.scenario, "feedback": feedback}
