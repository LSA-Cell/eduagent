"""AgentTodo 可见任务清单（增强机制 §2.4）。

todo_write 内置工具：多阶段子 Agent 任务（组卷/学情分析/PPT 生成/DreamTask）
的进度 checklist。同一时间仅允许 1 个 in_progress；全部 completed 前禁止产出
最终交付卡片（Loop 侧 card_gate 强制）；快照随 agent_runs.todo_snapshot 落库。
红线：Todo 仅作进度可视化与 Agent 自约束，禁止充当业务数据存储。
"""
