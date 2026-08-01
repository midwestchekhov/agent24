"""Small allow-listed evaluator for declarative explainer formulas.

It intentionally supports arithmetic only; generated strings never reach
``eval``. ``softmax`` is a named primitive because calibration examples use
``softmax(logits / T)``.
"""

from __future__ import annotations

import ast
import math
from typing import Mapping, Sequence


class FormulaError(ValueError):
    pass


def evaluate(expression: str, variables: Mapping[str, float | Sequence[float]]) -> float | list[float]:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise FormulaError("invalid formula") from exc
    return _eval(tree.body, variables)


def _eval(node: ast.AST, variables: Mapping[str, float | Sequence[float]]):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name) and node.id in variables:
        return variables[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval(node.operand, variables)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
        left, right = _eval(node.left, variables), _eval(node.right, variables)
        try:
            op_type = type(node.op)
            if isinstance(left, (list, tuple)) and isinstance(right, (int, float)):
                return [_scalar(op_type, value, right) for value in left]
            if isinstance(right, (list, tuple)) and isinstance(left, (int, float)):
                return [_scalar(op_type, left, value) for value in right]
            return _scalar(op_type, left, right)
        except (KeyError, TypeError, ZeroDivisionError, OverflowError) as exc:
            raise FormulaError("invalid formula operands") from exc
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        name = node.func.id
        args = [_eval(arg, variables) for arg in node.args]
        if name == "softmax" and len(args) == 1 and isinstance(args[0], (list, tuple)):
            values = [float(v) for v in args[0]]
            if not values:
                raise FormulaError("softmax needs at least one value")
            peak = max(values)
            exps = [math.exp(v - peak) for v in values]
            total = sum(exps)
            return [v / total for v in exps]
        if name in {"min", "max"} and args and all(isinstance(v, (int, float)) for v in args):
            return (min if name == "min" else max)(args)
        if name == "log" and len(args) == 1 and isinstance(args[0], (int, float)) and args[0] > 0:
            return math.log(args[0])
    raise FormulaError("operation is not allow-listed")


def _scalar(op_type, left, right):
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        raise FormulaError("formula operands must be numeric")
    return {
        ast.Add: lambda: left + right,
        ast.Sub: lambda: left - right,
        ast.Mult: lambda: left * right,
        ast.Div: lambda: left / right,
        ast.Pow: lambda: left ** right,
    }[op_type]()
