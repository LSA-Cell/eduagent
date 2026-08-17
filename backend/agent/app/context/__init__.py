"""四级上下文压缩管线：slim(L1) → microcompact(L2) → autocompact(L3) → reactive(L4)。

只作用于消息历史（dict 列表），纯函数为主；结构化业务表数据根本不进
消息历史，天然不在压缩范围内。
"""
