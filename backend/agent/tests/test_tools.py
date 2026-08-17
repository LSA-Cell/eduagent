"""工具系统测试：calculator 安全 AST、equation_solver、注册表。

注意：旧 chat_service.run_tool_loop（无护栏工具循环）已随 /agent/chat 下线删除，
工具执行链路的边界测试见 test_agent_loop.py / test_builtin_tools.py（ToolExecutor）。
"""

from __future__ import annotations

import pytest

import app.tools  # noqa: F401 - 触发内置工具注册
from app.tools.base import ToolContext, ToolError, ToolRegistry, registry
from app.tools.calculator import CalculatorError, handle_calculator, safe_eval
from app.tools.equation_solver import solve_equation

CTX = ToolContext(user_id="u-test")


# ---------------------------------------------------------------------------
# calculator：白名单 AST 求值
# ---------------------------------------------------------------------------
class TestCalculator:
    def test_basic_arithmetic(self) -> None:
        assert safe_eval("3*(4+5)/2") == 13.5
        assert safe_eval("2**10") == 1024
        assert safe_eval("10 % 3") == 1
        assert safe_eval("-7 + 3") == -4

    def test_functions_and_consts(self) -> None:
        assert safe_eval("sqrt(16)") == 4  # 整数结果归一为 int
        assert safe_eval("abs(-5)") == 5
        assert safe_eval("max(1, 9, 3)") == 9
        assert 3.14 < safe_eval("pi") < 3.15
        assert safe_eval("gcd(12, 18)") == 6

    @pytest.mark.parametrize(
        "expression",
        [
            "__import__('os').system('id')",  # 裸 eval 注入：黑名单外函数调用
            "().__class__.__bases__",  # 属性访问逃逸
            "open('/etc/passwd')",  # 非白名单函数
            "lambda: 1",  # 匿名函数
            "[i for i in range(10)]",  # 推导式
            "x = 1",  # 赋值语句（非表达式）
            "import os",  # import 语句
            "unknown_var + 1",  # 未定义标识符
            "'字符串'",  # 非数字字面量
            "True",  # 布尔字面量
        ],
    )
    def test_injection_rejected(self, expression: str) -> None:
        with pytest.raises(CalculatorError):
            safe_eval(expression)

    def test_pow_guard(self) -> None:
        with pytest.raises(CalculatorError):
            safe_eval("9**101")  # 指数超过上限，防止爆炸计算
        assert safe_eval("9**100") == 9**100

    def test_syntax_error(self) -> None:
        with pytest.raises(CalculatorError):
            safe_eval("3 +* 4")

    async def test_handler_error_capture(self) -> None:
        assert "error" in await handle_calculator({"expression": ""}, CTX)
        assert "error" in await handle_calculator({"expression": "1/0"}, CTX)
        ok = await handle_calculator({"expression": "1+1"}, CTX)
        assert ok == {"expression": "1+1", "result": 2}


# ---------------------------------------------------------------------------
# equation_solver：sympy 分步求解
# ---------------------------------------------------------------------------
class TestEquationSolver:
    def test_linear(self) -> None:
        result = solve_equation("2*x+3=7")
        assert result["solutions"] == ["2"]
        assert any("移项" in step for step in result["steps"])

    def test_quadratic(self) -> None:
        result = solve_equation("x^2-5*x+6=0")
        assert sorted(result["solutions"]) == ["2", "3"]

    def test_no_equal_sign_means_zero(self) -> None:
        result = solve_equation("x-5")
        assert result["solutions"] == ["5"]

    def test_no_real_solution(self) -> None:
        result = solve_equation("x - x + 1 = 0")  # 化简为 1=0，无解
        assert result["solutions"] == []

    def test_complex_roots_returned(self) -> None:
        # sympy 默认在复数域求解，实现不做实数过滤
        result = solve_equation("x^2+1=0")
        assert sorted(result["solutions"]) == ["-I", "I"]

    def test_invalid(self) -> None:
        from app.tools.equation_solver import EquationError

        with pytest.raises(EquationError):
            solve_equation("")
        with pytest.raises(EquationError):
            solve_equation("x +++= 1")


# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------
class TestToolRegistry:
    def test_register_and_get(self) -> None:
        reg = ToolRegistry()
        schema = {"name": "echo", "description": "回声", "parameters": {"type": "object"}}

        async def handler(args, ctx):
            return {"echo": args}

        reg.register_tool(schema, handler)
        assert reg.get("echo").description == "回声"
        assert reg.names() == ["echo"]

    def test_duplicate_register_rejected(self) -> None:
        reg = ToolRegistry()
        reg.register_tool({"name": "t"}, lambda a, c: {})
        with pytest.raises(ToolError):
            reg.register_tool({"name": "t"}, lambda a, c: {})

    def test_register_requires_name_and_callable(self) -> None:
        reg = ToolRegistry()
        with pytest.raises(ToolError):
            reg.register_tool({}, lambda a, c: {})
        with pytest.raises(ToolError):
            reg.register_tool({"name": "x"}, handler=None)  # type: ignore[arg-type]

    async def test_execute_unknown_tool_returns_error(self) -> None:
        result = await ToolRegistry().execute("nope", {}, CTX)
        assert "error" in result

    async def test_execute_sync_and_async_handlers(self) -> None:
        reg = ToolRegistry()
        reg.register_tool({"name": "sync"}, lambda args, ctx: {"v": 1})

        async def ahandler(args, ctx):
            return {"v": 2}

        reg.register_tool({"name": "async"}, ahandler)
        assert await reg.execute("sync", {}, CTX) == {"v": 1}
        assert await reg.execute("async", {}, CTX) == {"v": 2}

    async def test_execute_handler_exception_captured(self) -> None:
        reg = ToolRegistry()

        def boom(args, ctx):
            raise RuntimeError("炸了")

        reg.register_tool({"name": "boom"}, boom)
        result = await reg.execute("boom", {}, CTX)
        assert "炸了" in result["error"]

    def test_builtin_tools_registered(self) -> None:
        names = registry.names()
        assert names == ["calculator", "equation_solver", "knowledge_graph_query", "web_search"]
        schemas = registry.openai_schemas()
        assert all(s["type"] == "function" and s["function"]["name"] for s in schemas)

    async def test_builtin_calculator_via_registry(self) -> None:
        result = await registry.execute("calculator", {"expression": "6*7"}, CTX)
        assert result["result"] == 42

