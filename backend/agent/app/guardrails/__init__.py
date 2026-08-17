"""护栏决策链：moderation → role → quota → parental，任一非 allow 短路。

红线：执行器入口强制过链；审核服务故障 fail-closed；每条决策落审计
（fail-soft）；链不依赖工具内部状态（只看 EduTool 元数据 + TurnConfig + 入参）。
"""
