"""calculator 工具：安全 AST 求值（禁止裸 eval，白名单节点）。"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

from app.tools.base import ToolContext, registry

MAX_POW_EXPONENT = 100  # 防止 9**9**9 之类的爆炸计算


class CalculatorError(ValueError):
    """表达式非法或包含不允许的语法。"""


_BIN_OPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type, Any] = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS: dict[str, Any] = {
    "sqrt": math.sqrt, "abs": abs, "round": round, "min": min, "max": max,
    "floor": math.floor, "ceil": math.ceil, "pow": pow,
    "log": math.log, "log2": math.log2, "log10": math.log10, "exp": math.exp,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "gcd": math.gcd, "factorial": math.factorial,
}
_CONSTS: dict[str, float] = {"pi": math.pi, "e": math.e}


def _eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorError("只允许数字字面量")
        return node.value
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise CalculatorError(f"不支持的运算符: {type(node.op).__name__}")
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_POW_EXPONENT:
            raise CalculatorError(f"指数过大（>|{MAX_POW_EXPONENT}|），拒绝计算")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise CalculatorError(f"不支持的一元运算符: {type(node.op).__name__}")
        return op(_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise CalculatorError("只允许调用白名单数学函数")
        if node.keywords:
            raise CalculatorError("不支持关键字参数")
        return _FUNCS[node.func.id](*[_eval_node(a) for a in node.args])
    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise CalculatorError(f"未定义的标识符: {node.id}")
    raise CalculatorError(f"不支持的语法: {type(node).__name__}")


def safe_eval(expression: str) -> int | float:
    """对数学表达式做白名单 AST 求值；结果为整数时返回 int。"""
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"表达式语法错误: {exc.msg}") from exc
    result = _eval_node(tree)
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        return int(result)
    return result


CALCULATOR_SCHEMA: dict[str, Any] = {
    "name": "calculator",
    "description": "安全计算数学表达式（加减乘除、幂、取余及 sqrt/abs/round 等数学函数），禁止用于非计算任务。",
    "parameters": {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "数学表达式，如 3*(4+5)/2"}
        },
        "required": ["expression"],
    },
}


async def handle_calculator(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    expression = str(args.get("expression", "")).strip()
    if not expression:
        return {"error": "缺少 expression 参数"}
    try:
        return {"expression": expression, "result": safe_eval(expression)}
    except (CalculatorError, ArithmeticError) as exc:
        return {"error": str(exc)}


registry.register_tool(CALCULATOR_SCHEMA, handle_calculator)
