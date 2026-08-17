"""审计落库：agent_runs / tool_call_audits / guardrail_decisions / context_compactions。

全部为 fail-soft 薄写入：任何失败仅记日志，绝不影响主流程。
"""
