"""AskUserQuestion 结构化中途提问（增强机制 §2.2）。

- store：挂起状态存储（InMemory / Redis，TTL 120s），Loop 挂起等答复或超时；
- tool：内置工具 ask_user_question（协议校验 + 频次上限 + ask 事件）；
- policy：超时分级（普通澄清 → 推荐项默认继续并明示；敏感决策 → 按取消）。
"""
