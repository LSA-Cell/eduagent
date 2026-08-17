"""护栏决策值对象。"""

from __future__ import annotations

from dataclasses import dataclass

ALLOW = "allow"
ASK = "ask"
DENY = "deny"


@dataclass(frozen=True)
class Decision:
    """单个护栏环节/整条链的决策：decision ∈ allow/ask/deny + 环节 + 理由。"""

    decision: str
    stage: str = ""
    reason: str = ""

    @classmethod
    def allow(cls, stage: str = "") -> "Decision":
        return cls(decision=ALLOW, stage=stage)

    @classmethod
    def ask(cls, stage: str, reason: str) -> "Decision":
        return cls(decision=ASK, stage=stage, reason=reason)

    @classmethod
    def deny(cls, stage: str, reason: str) -> "Decision":
        return cls(decision=DENY, stage=stage, reason=reason)
