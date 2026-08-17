"""Plan Mode 两段式生成（增强机制 §2.1）。

- state_machine：drafting → awaiting_confirm → confirmed → generating → completed，
  旁路 cancelled / expired；非法迁移抛 PlanStateError；
- manager：PlanManager（大纲草稿/提交确认/confirm_token 签发校验/修改产生新版本）；
- dispatch：confirmed 前置校验 + outline_snapshot 随 Celery 投递（红线 1/2/3）；
- tool：内置工具 submit_plan_outline（大纲卡片 + plan.outline/plan.status 事件）。
"""
