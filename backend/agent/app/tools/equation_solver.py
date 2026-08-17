"""equation_solver 工具：sympy 解方程并返回教学步骤。"""

from __future__ import annotations

from typing import Any

import sympy

from app.tools.base import ToolContext, registry


class EquationError(ValueError):
    """方程无法解析或求解。"""


def _sympify(text: str, symbol: sympy.Symbol) -> sympy.Expr:
    """把用户输入转成 sympy 表达式（^ 视为幂，1/2 保留为有理数）。"""
    try:
        return sympy.sympify(
            text.strip().replace("^", "**"),
            locals={str(symbol): symbol},
            rational=True,
        )
    except (sympy.SympifyError, TypeError, ValueError) as exc:
        raise EquationError(f"无法解析表达式: {text}") from exc


def solve_equation(equation: str, variable: str = "x") -> dict[str, Any]:
    """解方程，返回 {"variable", "steps", "solutions"}。

    equation 支持 "2*x+3=7"、"x^2-5x+6=0"，或不带等号时视为 "=0"。
    """
    equation = equation.strip()
    if not equation:
        raise EquationError("方程不能为空")
    x = sympy.Symbol(variable)
    steps: list[str] = []

    if "=" in equation:
        lhs_text, rhs_text = equation.split("=", 1)
        lhs, rhs = _sympify(lhs_text, x), _sympify(rhs_text, x)
        steps.append(f"原方程：{sympy.Eq(lhs, rhs)}")
        standard = lhs - rhs
        steps.append(f"移项，化为标准形式：{standard} = 0")
    else:
        standard = _sympify(equation, x)
        steps.append(f"原式（视为等于 0）：{standard} = 0")

    simplified = sympy.simplify(standard)
    if simplified != standard:
        steps.append(f"化简：{simplified} = 0")

    try:
        solutions = sympy.solve(simplified, x)
    except Exception as exc:  # sympy 对部分方程会抛 NotImplementedError 等
        raise EquationError(f"求解失败: {exc}") from exc

    if not solutions:
        if simplified == 0:
            steps.append("方程恒成立，解为全体实数")
            solutions_text = ["全体实数"]
        else:
            steps.append("方程在实数范围内无解")
            solutions_text = []
    else:
        solutions_text = [str(s) for s in solutions]
        steps.append(f"求解得：{variable} = {', '.join(solutions_text)}")

    return {"variable": variable, "steps": steps, "solutions": solutions_text}


EQUATION_SOLVER_SCHEMA: dict[str, Any] = {
    "name": "equation_solver",
    "description": "用 sympy 解一元方程（含一元一次/二次等），返回分步求解过程与解集。",
    "parameters": {
        "type": "object",
        "properties": {
            "equation": {"type": "string", "description": "方程，如 2*x+3=7 或 x^2-5x+6=0"},
            "variable": {"type": "string", "description": "未知数符号，默认 x", "default": "x"},
        },
        "required": ["equation"],
    },
}


async def handle_equation_solver(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    equation = str(args.get("equation", "")).strip()
    variable = str(args.get("variable", "x") or "x")
    try:
        return solve_equation(equation, variable)
    except EquationError as exc:
        return {"error": str(exc)}


registry.register_tool(EQUATION_SOLVER_SCHEMA, handle_equation_solver)
