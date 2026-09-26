"""Maths expressions, evaluated safely and all at once.

The shape language lets you write real maths anywhere a number is expected:

    curve x = radius * (1 - 0.3 * t)   y = height * t

Those strings are parsed to a syntax tree and walked by hand — no `eval`, no imports,
no attribute access, no subscripts. Only numbers, the names in scope and the functions
below can appear, so an expression coming from anywhere (a file, an assistant, the
network) can be evaluated without handing over the process.

Values may be numpy arrays, so a whole curve is evaluated in one pass: pass
``t = np.linspace(0, 1, steps)`` and get every point back at once.
"""

from __future__ import annotations

import ast
import math

import numpy as np

from ..sdf_code import SdfCodeError


class ExprError(SdfCodeError):
    """A problem with an expression, worded for whoever wrote it.

    Shares a base with the GLSL-side errors so every layer above — the panel, the API,
    the MCP server — reports all of them the same way.
    """


def _clamp(x, lo=0.0, hi=1.0):
    return np.clip(x, lo, hi)


def _mix(a, b, t):
    return np.asarray(a) * (1.0 - np.asarray(t)) + np.asarray(b) * np.asarray(t)


def _smoothstep(edge0, edge1, x):
    t = np.clip((np.asarray(x) - edge0) / np.where(np.asarray(edge1) - np.asarray(edge0) == 0,
                                                   1e-12, np.asarray(edge1) - np.asarray(edge0)), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


FUNCTIONS = {
    "sin": np.sin, "cos": np.cos, "tan": np.tan,
    "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan, "atan2": np.arctan2,
    "sqrt": np.sqrt, "abs": np.abs, "sign": np.sign,
    "floor": np.floor, "ceil": np.ceil, "round": np.round,
    "exp": np.exp, "log": np.log, "pow": np.power,
    "min": np.minimum, "max": np.maximum, "mod": np.mod, "hypot": np.hypot,
    "clamp": _clamp, "mix": _mix, "lerp": _mix, "smoothstep": _smoothstep,
    "radians": np.radians, "degrees": np.degrees,
}

CONSTANTS = {"pi": math.pi, "tau": math.tau, "e": math.e}

_BINOPS = {
    ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply,
    ast.Div: np.divide, ast.FloorDiv: np.floor_divide, ast.Mod: np.mod, ast.Pow: np.power,
}
_COMPARE = {
    ast.Lt: np.less, ast.LtE: np.less_equal, ast.Gt: np.greater,
    ast.GtE: np.greater_equal, ast.Eq: np.equal, ast.NotEq: np.not_equal,
}


def evaluate(source, scope=None):
    """Evaluate one expression. `scope` maps names to numbers or numpy arrays."""
    if isinstance(source, (int, float, np.ndarray)):
        return source
    text = str(source).strip()
    if not text:
        raise ExprError("the expression is empty")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"{text!r} is not a valid expression: {exc.msg}") from None
    names = dict(CONSTANTS)
    names.update(scope or {})
    return _walk(tree.body, names, text)


def names_used(source):
    """Every name an expression refers to — for telling someone about a typo early."""
    try:
        tree = ast.parse(str(source), mode="eval")
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def _walk(node, names, text):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ExprError(f"{node.value!r} is not a number")
        return float(node.value)

    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        known = ", ".join(sorted(k for k in names if not k.startswith("_"))) or "nothing"
        raise ExprError(f"unknown name '{node.id}' in {text!r}. In scope: {known}")

    if isinstance(node, ast.BinOp):
        op = _BINOPS.get(type(node.op))
        if op is None:
            raise ExprError(f"the operator in {text!r} isn't allowed here")
        left, right = _walk(node.left, names, text), _walk(node.right, names, text)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            return op(left, right)

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.USub):
            return np.negative(_walk(node.operand, names, text))
        if isinstance(node.op, ast.UAdd):
            return _walk(node.operand, names, text)
        if isinstance(node.op, ast.Not):
            return np.logical_not(_walk(node.operand, names, text))
        raise ExprError(f"that operator isn't allowed in {text!r}")

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ExprError(f"only plain function calls are allowed in {text!r}")
        fn = FUNCTIONS.get(node.func.id)
        if fn is None:
            raise ExprError(f"unknown function '{node.func.id}'. Available: "
                            + ", ".join(sorted(FUNCTIONS)))
        if node.keywords:
            raise ExprError(f"'{node.func.id}' takes plain arguments, not name=value")
        args = [_walk(a, names, text) for a in node.args]
        try:
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                return fn(*args)
        except TypeError as exc:
            raise ExprError(f"{node.func.id}: {exc}") from None

    if isinstance(node, ast.Compare):
        if len(node.ops) != 1:
            raise ExprError("chained comparisons aren't supported")
        op = _COMPARE.get(type(node.ops[0]))
        if op is None:
            raise ExprError(f"that comparison isn't allowed in {text!r}")
        return op(_walk(node.left, names, text), _walk(node.comparators[0], names, text))

    if isinstance(node, ast.BoolOp):
        op = np.logical_and if isinstance(node.op, ast.And) else np.logical_or
        result = _walk(node.values[0], names, text)
        for value in node.values[1:]:
            result = op(result, _walk(value, names, text))
        return result

    if isinstance(node, ast.IfExp):        # a if condition else b
        return np.where(_walk(node.test, names, text),
                        _walk(node.body, names, text),
                        _walk(node.orelse, names, text))

    raise ExprError(f"{type(node).__name__} isn't allowed in an expression ({text!r})")


def number(source, scope=None, name="value"):
    """Evaluate to a single float, complaining if it isn't one."""
    value = evaluate(source, scope)
    array = np.asarray(value, dtype=float)
    if array.ndim != 0:
        raise ExprError(f"{name} must be a single number, got {array.size} values")
    if not np.isfinite(array):
        raise ExprError(f"{name} came out as {array} — check the maths")
    return float(array)


def integer(source, scope=None, name="value", low=None, high=None):
    value = number(source, scope, name)
    result = int(round(value))
    if low is not None and result < low:
        raise ExprError(f"{name} must be at least {low} (got {result})")
    if high is not None and result > high:
        raise ExprError(f"{name} must be at most {high} (got {result})")
    return result
