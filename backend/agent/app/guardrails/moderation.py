"""环节 ① 内容审核：经 Deps 调推理网关小模型，异常 fail-closed（deny）。

注意教育场景不得误伤生物/医学正常术语（提示词层面约束，由网关侧
小模型 + moderation prompt 承担；本模块只做调用与失败收敛）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.guardrails.decision import Decision

logger = logging.getLogger(__name__)

MAX_MODERATION_CHARS = 2000


class ModerationStage:
    """内容审核环节；deps.moderate(text) 返回 True=安全，异常 → deny。"""

    name = "moderation"

    def __init__(self, deps: Any) -> None:
        self._deps = deps

    async def check(self, tool: Any, config: Any, arguments: dict[str, Any]) -> Decision:
        text = json.dumps(arguments or {}, ensure_ascii=False, default=str)[:MAX_MODERATION_CHARS]
        try:
            safe = await self._deps.moderate(text)
        except Exception as exc:  # noqa: BLE001 - 审核服务故障一律 fail-closed
            logger.warning("内容审核服务不可用，fail-closed deny", exc_info=True)
            return Decision(
                decision="deny", stage=self.name,
                reason=f"内容审核服务不可用（fail-closed）: {type(exc).__name__}",
            )
        if not safe:
            return Decision(decision="deny", stage=self.name, reason="内容审核未通过")
        return Decision.allow(self.name)
