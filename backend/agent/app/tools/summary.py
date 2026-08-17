"""ToolUseSummary 工具摘要（增强机制 §3.2）：≤50 字一句话摘要，双用途。

① UI 展示（"正在使用 作业批改…"的后续说明，挂在 ToolExecution.summary）；
② 压缩/瘦身占位文本（执行器 _slim 的占位前缀改走本模块）。

生成策略：模板优先（工具名+关键参数+结果状态，全覆盖内置工具）；
模板未覆盖时经推理网关小模型生成（≤100ms wait_for），失败/超时回退
通用模板兜底。

红线：
- 摘要禁止包含用户隐私原文——模板只拼接关键参数的截断值；小模型路径
  只喂元信息（参数键/结果状态/结果长度），原始内容根本不进 prompt；
- 模板命中率 ≥80%（注入 SummaryStats 计数器可断言）。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 50
LLM_SUMMARY_TIMEOUT_S = 0.1  # 小模型路径单次 ≤100ms（红线）
_ARG_VALUE_CHARS = 12  # 关键参数截断长度

_FAIL_MARKERS = ("失败", "未成功", "超时", "已取消", "被拒绝", "暂不可用", "仅教师")

_LLM_SYSTEM = (
    "你是工具调用摘要器。根据给定的工具元信息，用不超过 40 个字的一句话"
    "描述这次调用做了什么（只说元信息，如“计算数学表达式”“联网搜索资料”），"
    "禁止编造细节，禁止输出任何引文。只输出摘要本身。"
)


@dataclass
class SummaryStats:
    """摘要生成统计（模板命中率 = template_hits / 总次数）。"""

    template_hits: int = 0
    llm_attempts: int = 0
    llm_failures: int = 0

    @property
    def total(self) -> int:
        return self.template_hits + self.llm_attempts

    @property
    def template_hit_rate(self) -> float:
        return self.template_hits / self.total if self.total else 1.0


def _content_of(result: Any) -> str:
    """result 兼容 str / ToolResult / ToolExecution（鸭子类型）。"""
    return str(getattr(result, "content", result) or "")


def _status_of(content: str) -> str:
    head = content[:60]
    return "未成功" if any(marker in head for marker in _FAIL_MARKERS) else "完成"


def _short(value: Any, limit: int = _ARG_VALUE_CHARS) -> str:
    """关键参数截断（只保留前 limit 字符，长文本永不整段进摘要）。"""
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# 模板表：覆盖全部内置工具（含 Wave C 交互工具与 tool_search）
# ---------------------------------------------------------------------------
def _tpl_calculator(args: dict[str, Any], status: str) -> str:
    return f"计算表达式（{_short(args.get('expression'))}）：{status}"


def _tpl_equation_solver(args: dict[str, Any], status: str) -> str:
    return f"解方程（{_short(args.get('equation'))}）：{status}"


def _tpl_web_search(args: dict[str, Any], status: str) -> str:
    return f"联网搜索（{_short(args.get('query'))}）：{status}"


def _tpl_knowledge_graph(args: dict[str, Any], status: str) -> str:
    return f"查询知识点（{_short(args.get('concept'))}）：{status}"


def _tpl_ocr(args: dict[str, Any], status: str) -> str:
    source = "图片" if args.get("image_url") else "文件"
    return f"识别{source}文字与公式：{status}"


def _tpl_mistake(args: dict[str, Any], status: str) -> str:
    return f"记录错题到错题本：{status}"


def _tpl_flashcard(args: dict[str, Any], status: str) -> str:
    count = args.get("count")
    return f"生成记忆卡片（{count} 张）：{status}" if count else f"生成记忆卡片：{status}"


def _tpl_dispatch(args: dict[str, Any], status: str) -> str:
    return f"派发子任务（{_short(args.get('agent'))}）：{status}"


def _tpl_ask(args: dict[str, Any], status: str) -> str:
    questions = args.get("questions")
    n = len(questions) if isinstance(questions, list) else 1
    return f"向用户提问（{n} 题）：{status}"


def _tpl_todo(args: dict[str, Any], status: str) -> str:
    items = args.get("items")
    n = len(items) if isinstance(items, list) else 0
    return f"更新任务清单（{n} 项）：{status}" if n else f"更新任务清单：{status}"


def _tpl_plan_outline(args: dict[str, Any], status: str) -> str:
    return "提交大纲等待确认：完成"


def _tpl_tool_search(args: dict[str, Any], status: str) -> str:
    return f"搜索可用工具（{_short(args.get('query'))}）：{status}"


TEMPLATES: dict[str, Callable[[dict[str, Any], str], str]] = {
    "calculator": _tpl_calculator,
    "equation_solver": _tpl_equation_solver,
    "web_search": _tpl_web_search,
    "knowledge_graph_query": _tpl_knowledge_graph,
    "ocr_extract": _tpl_ocr,
    "mistake_recorder": _tpl_mistake,
    "flashcard_generator": _tpl_flashcard,
    "sub_agent_dispatch": _tpl_dispatch,
    "ask_user_question": _tpl_ask,
    "todo_write": _tpl_todo,
    "submit_plan_outline": _tpl_plan_outline,
    "tool_search": _tpl_tool_search,
}


def _cap(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= MAX_SUMMARY_CHARS else text[: MAX_SUMMARY_CHARS - 1] + "…"


def _generic_template(tool_name: str, status: str) -> str:
    return _cap(f"使用工具 {tool_name}：{status}")


async def _llm_summary(
    gateway: Any,
    *,
    tool_name: str,
    args: dict[str, Any],
    status: str,
    result_chars: int,
    model: Optional[str],
) -> str:
    """小模型路径：只喂元信息（原始内容禁止进 prompt），≤100ms 超时。"""
    meta = (
        f"工具名：{tool_name}\n参数键：{sorted(args.keys())}\n"
        f"结果状态：{status}\n结果长度：{result_chars} 字符"
    )
    result = await asyncio.wait_for(
        gateway.chat(
            [
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user", "content": meta},
            ],
            model=model,
            max_tokens=60,
        ),
        timeout=LLM_SUMMARY_TIMEOUT_S,
    )
    return _cap(str(getattr(result, "content", "") or ""))


async def summarize_tool_result(
    tool_name: str,
    args: Optional[dict[str, Any]],
    result: Any,
    *,
    gateway: Any = None,
    model: Optional[str] = None,
    stats: Optional[SummaryStats] = None,
) -> str:
    """生成 ≤50 字摘要。模板命中走模板；否则小模型（失败回退通用模板）。"""
    args = dict(args or {})
    content = _content_of(result)
    status = _status_of(content)

    template = TEMPLATES.get(tool_name)
    if template is not None:
        if stats is not None:
            stats.template_hits += 1
        return _cap(template(args, status))

    if stats is not None:
        stats.llm_attempts += 1
    if gateway is not None:
        try:
            text = await _llm_summary(
                gateway,
                tool_name=tool_name,
                args=args,
                status=status,
                result_chars=len(content),
                model=model,
            )
            if text:
                return text
        except Exception:  # noqa: BLE001 - 超时/异常一律回退模板兜底
            logger.info("工具摘要小模型生成失败（%s），回退模板", tool_name)
    if stats is not None:
        stats.llm_failures += 1
    return _generic_template(tool_name, status)


__all__ = [
    "MAX_SUMMARY_CHARS",
    "LLM_SUMMARY_TIMEOUT_S",
    "SummaryStats",
    "TEMPLATES",
    "summarize_tool_result",
]
