"""Stop Hooks 收尾校验（增强机制 §2.5）。

模型输出 stop 意图时按上下文匹配校验器；失败原因作为 blocking 消息注入
Loop 强制继续（同一 run ≤2 次），第 3 次仍失败带 warning 标记交付并落
stop_hook_audits。校验器只读上下文 + 沙箱计算，单次 ≤2s（service 层
asyncio.wait_for 强制），禁止写业务表。
"""
