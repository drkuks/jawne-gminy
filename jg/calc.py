"""Calculator for `derived` indicators from `metrics.yaml` (one definition for all gminy).

A formula is an arithmetic expression over indicator ids (+ - * /, parentheses, numbers,
round/min/max/abs functions). Evaluated via the AST, without `eval`. The result stores the
formula and input values. Any missing input or division by zero = `brak danych` with the reason
(never 0).
"""

from __future__ import annotations

import ast
import functools
import operator
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, DivisionByZero, InvalidOperation

from jg.metrics import Metric

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_FUNCS = {"min": min, "max": max, "abs": abs}


class FormulaError(Exception):
    pass


@dataclass(frozen=True)
class Wynik:
    kod: str
    wartosc: Decimal | None
    status: str
    wzor: str
    wejscie: dict[str, Decimal | None]


def _eval(node: ast.AST, env: dict[str, Decimal]) -> Decimal:
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    ):
        return Decimal(str(node.value))
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left, env), _eval(node.right, env))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand, env)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        args = [_eval(a, env) for a in node.args]
        if node.func.id == "round" and len(args) == 2:
            return args[0].quantize(Decimal(1).scaleb(-int(args[1])), rounding=ROUND_HALF_UP)
        if node.func.id in _FUNCS:
            return _FUNCS[node.func.id](*args)
    raise FormulaError(f"niedozwolony element wzoru: {ast.dump(node)[:80]}")


@functools.lru_cache(maxsize=256)
def _parse(formula: str) -> ast.Expression:
    return ast.parse(formula, mode="eval")


def oblicz(
    metric: Metric, wejscie: dict[str, Decimal | None], przyczyny: dict[str, str] | None = None
) -> Wynik:
    """Value of a `derived` indicator for one unit and year. `przyczyny`: reason a given
    input is missing (carried into the result status)."""
    assert metric.kind == "derived" and metric.formula
    values = {k: wejscie.get(k) for k in metric.inputs}
    brak = sorted(k for k, v in values.items() if v is None)
    if brak:
        opisy = [f"{k}: {(przyczyny or {}).get(k, 'brak wartości')}" for k in brak]
        return Wynik(
            metric.id, None, f"brak danych: wejście {'; '.join(opisy)}", metric.formula, values
        )
    tree = _parse(metric.formula)
    try:
        wynik = _eval(tree, values)  # type: ignore[arg-type]
    except (ZeroDivisionError, DivisionByZero):
        return Wynik(metric.id, None, "brak danych: dzielenie przez zero", metric.formula, values)
    except InvalidOperation as exc:
        return Wynik(
            metric.id,
            None,
            f"brak danych: błąd obliczenia ({type(exc).__name__})",
            metric.formula,
            values,
        )
    return Wynik(metric.id, wynik, "ok", metric.formula, values)
