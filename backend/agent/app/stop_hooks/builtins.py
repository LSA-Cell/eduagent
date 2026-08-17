"""3 个内置 Stop Hook 校验器（增强机制 §2.5）。

- math_verify：数值/方程类答案经 SymPy 沙箱复核（方程解回代 + 数值等值）；
- grading_consistency：批改卡片结论与引用的标准答案/评分细则一致性；
- completeness_check：教案六模块 / 试卷题量分值答案解析 / PPT 结构完整性。

全部为确定性代码（sympy 为既有依赖），无网络依赖；单题校验由
service 层 wait_for ≤2s 强制；只读 StopHookContext，不写任何表。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from app.stop_hooks.base import StopHookContext, StopHookResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# math_verify
# ---------------------------------------------------------------------------
# 答案中的求解声明：x = 2 / x=-3 / x = 1/2（变量为 1-2 个拉丁字母）
_CLAIM_RE = re.compile(r"\b([a-zA-Z]\w{0,1})\s*=\s*(-?[0-9][0-9./]*(?:\s*[0-9./]+)?)")
# 题面中的方程：含变量与等号的片段（如 2x+3=7、x^2-4=0）
_EQUATION_RE = re.compile(r"[0-9a-zA-Z+\-*/^().\s]{1,60}=[0-9a-zA-Z+\-*/^().\s]{0,60}")


def _sympify(text: str) -> Any:
    """沙箱化解析：^ → **，sympify 失败抛异常由调用方收敛。"""
    import sympy

    return sympy.sympify(text.replace("^", "**").replace(" ", ""), rational=True)


def _check_equation_claims(
    claims: list[tuple[str, str]], source_text: str
) -> Optional[dict[str, Any]]:
    """对每个含同变量的方程做解回代；返回失败细节或 None（全部通过/无可校验）。

    判定：同一变量若能找到方程，则该变量至少一个声明值回代成立才算通过。
    """
    import sympy

    equations: dict[str, list[Any]] = {}
    for match in _EQUATION_RE.finditer(source_text):
        seg = match.group(0)
        lhs, _, rhs = seg.partition("=")
        try:
            expr = _sympify(lhs) - _sympify(rhs or "0")
        except Exception:  # noqa: BLE001 - 非数学片段跳过
            continue
        for var in expr.free_symbols:
            equations.setdefault(str(var), []).append(expr)

    checked: dict[str, Any] = {"equations": {k: len(v) for k, v in equations.items()}, "claims": []}
    failures: list[dict[str, Any]] = []
    verified_any = False
    for var, value in claims:
        exprs = equations.get(var)
        if not exprs:
            continue  # 题面无该变量方程 → 无法复核，不拦截
        try:
            symbol = sympy.Symbol(var)
            claimed = _sympify(value)
        except Exception:  # noqa: BLE001
            continue
        ok = False
        for expr in exprs:
            try:
                if sympy.simplify(expr.subs(symbol, claimed)) == 0:
                    ok = True
                    break
            except Exception:  # noqa: BLE001
                continue
        verified_any = True
        checked["claims"].append({"var": var, "value": value, "ok": ok})
        if not ok:
            failures.append({"var": var, "value": value})
    if not verified_any:
        return None  # 无可复核内容
    if failures:
        return {
            "reason": "、".join(f"方程解 {f['var']} = {f['value']} 回代不成立" for f in failures),
            "detail": checked,
        }
    return None


class MathVerifyHook:
    """数值/方程类答案 SymPy 复核（沙箱：to_thread + 外层 2s wait_for）。"""

    name = "math_verify"

    def applies(self, ctx: StopHookContext) -> bool:
        # 答案中有 "变量 = 数值" 声明才需要复核
        return bool(_CLAIM_RE.search(ctx.answer_text))

    async def validate(self, ctx: StopHookContext) -> StopHookResult:
        claims = [(m.group(1), m.group(2).strip()) for m in _CLAIM_RE.finditer(ctx.answer_text)]
        # 去掉单位尾巴（如 "2 小时" 中的 "2" 后随中文已在正则边界外）
        source = f"{ctx.user_text}\n{ctx.answer_text}"
        try:
            failure = await asyncio.to_thread(_check_equation_claims, claims, source)
        except Exception:  # noqa: BLE001 - sympy 异常按无法复核放行（不误伤）
            logger.warning("math_verify sympy 复核异常，放行", exc_info=True)
            return StopHookResult(passed=True, detail={"skipped": "sympy_error"})
        if failure is None:
            return StopHookResult(passed=True, detail={"claims_checked": len(claims)})
        return StopHookResult(
            passed=False,
            reason=f"数学答案复核未通过：{failure['reason']}。请重新验算并给出正确答案。",
            detail=failure["detail"],
        )


# ---------------------------------------------------------------------------
# grading_consistency
# ---------------------------------------------------------------------------
_VALID_VERDICTS = {"correct", "wrong", "partial", "正确", "错误", "半对"}


class GradingConsistencyHook:
    """批改结论一致性：每题须有合法 verdict 且引用标准答案/评分细则。"""

    name = "grading_consistency"

    def applies(self, ctx: StopHookContext) -> bool:
        return any(c.get("card_type") == "grading" for c in ctx.cards)

    async def validate(self, ctx: StopHookContext) -> StopHookResult:
        problems: list[str] = []
        checked = 0
        for card in ctx.cards:
            if card.get("card_type") != "grading":
                continue
            data = card.get("card_data") or {}
            items = data.get("results") or data.get("items") or []
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                checked += 1
                verdict = str(item.get("verdict") or item.get("result") or "")
                reference = item.get("standard_answer") or item.get("rubric") or item.get("explanation")
                if verdict not in _VALID_VERDICTS:
                    problems.append(f"第 {index + 1} 题批改结论缺失或非法（verdict={verdict!r}）")
                if not reference:
                    problems.append(f"第 {index + 1} 题未引用标准答案/评分细则")
        if problems:
            return StopHookResult(
                passed=False,
                reason="批改一致性校验未通过：" + "；".join(problems[:3]) + "。请补全后重新给出批改结论。",
                detail={"problems": problems, "checked": checked},
            )
        return StopHookResult(passed=True, detail={"checked": checked})


# ---------------------------------------------------------------------------
# completeness_check
# ---------------------------------------------------------------------------
_LESSON_PLAN_MODULES = {
    "教学目标": ("教学目标", "objectives"),
    "重难点": ("重难点", "重点", "难点", "key_points", "difficulties"),
    "教学过程": ("教学过程", "procedure"),
    "板书": ("板书", "blackboard"),
    "作业": ("作业", "homework"),
    "教学反思": ("反思", "reflection"),
}
_EXAM_REQUIRED = {
    "题量": ("题量", "共", "count", "questions"),
    "分值合计": ("分值", "总分", "满分", "score", "points"),
    "答案": ("答案", "answer"),
    "解析": ("解析", "explanation", "analysis"),
}
_PPT_REQUIRED = {
    "标题页": ("标题页", "title"),
    "目录": ("目录", "contents", "outline"),
    "正文": ("正文", "slides", "pages", "body"),
    "总结页": ("总结", "summary"),
}

_SKILL_KIND = {
    "lesson_plan": ("教案", _LESSON_PLAN_MODULES),
    "exam_paper": ("试卷", _EXAM_REQUIRED),
    "ppt": ("PPT", _PPT_REQUIRED),
}
_CARD_KIND = {
    "lesson_plan_preview": ("教案", _LESSON_PLAN_MODULES),
    "exam_preview": ("试卷", _EXAM_REQUIRED),
    "ppt_preview": ("PPT", _PPT_REQUIRED),
}


class CompletenessCheckHook:
    """长内容完整性：教案六模块 / 试卷题量分值答案解析 / PPT 四类页齐全。"""

    name = "completeness_check"

    def _kind(self, ctx: StopHookContext) -> Optional[tuple[str, dict[str, tuple[str, ...]]]]:
        if ctx.skill_code in _SKILL_KIND:
            return _SKILL_KIND[ctx.skill_code]
        for card in ctx.cards:
            card_type = card.get("card_type")
            if card_type in _CARD_KIND:
                return _CARD_KIND[card_type]
        return None

    def applies(self, ctx: StopHookContext) -> bool:
        return self._kind(ctx) is not None

    async def validate(self, ctx: StopHookContext) -> StopHookResult:
        kind = self._kind(ctx)
        assert kind is not None
        label, required = kind
        text = ctx.combined_text().lower()
        missing = [
            name
            for name, keywords in required.items()
            if not any(k.lower() in text for k in keywords)
        ]
        if missing:
            return StopHookResult(
                passed=False,
                reason=f"{label}完整性校验未通过，缺少：{'、'.join(missing)}。请补全后重新交付。",
                detail={"kind": label, "missing": missing},
            )
        return StopHookResult(passed=True, detail={"kind": label, "modules": sorted(required)})
