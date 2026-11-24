"""`metrics.yaml` schema: indicator definitions (ported from okiemnaolawe `oko/metrics.py`).

`fact` comes directly from the source (a report position), `derived` is computed by a formula
from other indicators. One definition for all gminy.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Kind = Literal["fact", "derived"]
Unit = Literal["zł", "zł/mieszk.", "%", "osoba", "szt."]
Period = Literal["rok", "kwartal", "dzien"]
Format = Literal["kwota", "procent", "liczba"]
EntityKind = Literal["gmina", "spolka"]

IDENTIFIER = re.compile(r"(?<![\w.])[A-Za-z_][A-Za-z0-9_]*")
FUNCTIONS = {"round", "min", "max", "abs"}


_ALLOWED = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Name,
    ast.Load,
    ast.Call,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.USub,
)


def check_formula(formula: str) -> None:
    """Formula: numbers, names, + - * /, unary minus, round(x, n), min, max, abs."""
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"wzór {formula!r}: błąd składni") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, int | float):
                raise ValueError(f"wzór {formula!r}: niedozwolona stała {node.value!r}")
        elif not isinstance(node, _ALLOWED):
            raise ValueError(f"wzór {formula!r}: niedozwolony element {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                raise ValueError(f"wzór {formula!r}: niedozwolona funkcja")
            if node.keywords:
                raise ValueError(f"wzór {formula!r}: argumenty nazwane")
            if node.func.id == "round" and (
                len(node.args) != 2 or not isinstance(node.args[1], ast.Constant)
            ):
                raise ValueError(f"wzór {formula!r}: round(x, n) wymaga dwóch argumentów")


def formula_names(formula: str) -> set[str]:
    """Indicator names used in the formula (excluding functions and numbers, e.g. `1e3`)."""
    without_numbers = re.sub(r"\d+(\.\d+)?([eE][+-]?\d+)?", " ", formula)
    return set(IDENTIFIER.findall(without_numbers)) - FUNCTIONS


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetricSource(_Model):
    source_id: str = Field(pattern=r"^S[0-9]+$")
    document: str
    locator: str
    note: str | None = None


class ControlValue(_Model):
    """Value transcribed by hand from the source document, for parser tests."""

    entity: str
    year: int = Field(ge=1990, le=2100)
    value: float
    origin: str


class Metric(_Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    kind: Kind
    unit: Unit
    period: Period
    format: Format
    entity_kinds: list[EntityKind] = Field(min_length=1)
    definition: str
    sources: list[MetricSource] = Field(default_factory=list)
    formula: str | None = None
    inputs: list[str] = Field(default_factory=list)
    control_values: list[ControlValue] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def _kind_fields(self) -> Metric:
        if self.kind == "fact":
            if not self.sources:
                raise ValueError(f"wskaźnik {self.id}: `fact` wymaga co najmniej jednego źródła")
            if self.formula or self.inputs:
                raise ValueError(f"wskaźnik {self.id}: `fact` nie ma wzoru ani wejść")
            return self
        if not self.formula or not self.inputs:
            raise ValueError(f"wskaźnik {self.id}: `derived` wymaga `formula` i `inputs`")
        if self.id in self.inputs:
            raise ValueError(f"wskaźnik {self.id}: wzór nie może odwoływać się do siebie")
        check_formula(self.formula)
        used = formula_names(self.formula)
        unknown = sorted(used - set(self.inputs))
        if unknown:
            raise ValueError(f"wskaźnik {self.id}: wzór używa nazw spoza `inputs`: {unknown}")
        missing = sorted(set(self.inputs) - used)
        if missing:
            raise ValueError(f"wskaźnik {self.id}: wejścia nieużyte we wzorze: {missing}")
        return self


class ProgiLudnosci(_Model):
    przedzialy_ludnosci: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def _rosnace(self) -> ProgiLudnosci:
        p = self.przedzialy_ludnosci
        if any(p[i + 1] <= p[i] for i in range(len(p) - 1)):
            raise ValueError("progi ludności muszą rosnąć")
        return self


class GrupyDef(ProgiLudnosci):
    """Comparison groups: gmina type × population band; cities with powiat status
    (miasta na prawach powiatu) have their own thresholds (`mnpp`)."""

    definicja: str
    min_n: int = Field(ge=2)
    mnpp: ProgiLudnosci
    porownawcze: list[str] = Field(min_length=1)


class MetricsFile(_Model):
    metrics: list[Metric] = Field(default_factory=list)
    grupy: GrupyDef | None = None

    @model_validator(mode="after")
    def _consistency(self) -> MetricsFile:
        ids: set[str] = set()
        for metric in self.metrics:
            if metric.id in ids:
                raise ValueError(f"powtórzony id wskaźnika: {metric.id}")
            ids.add(metric.id)
        for metric in self.metrics:
            for ref in metric.inputs:
                if ref not in ids:
                    raise ValueError(f"wskaźnik {metric.id}: nieznane wejście {ref}")
        self._check_cycles()
        if self.grupy:
            for kod in self.grupy.porownawcze:
                if kod not in ids:
                    raise ValueError(f"grupy.porownawcze: nieznany wskaźnik {kod}")
        return self

    def _check_cycles(self) -> None:
        graph = {m.id: m.inputs for m in self.metrics}
        state: dict[str, int] = {}  # 1 = in progress, 2 = checked

        def visit(node: str, path: list[str]) -> None:
            if state.get(node) == 2:
                return
            if state.get(node) == 1:
                raise ValueError(f"cykl we wzorach: {' → '.join([*path, node])}")
            state[node] = 1
            for ref in graph.get(node, []):
                visit(ref, [*path, node])
            state[node] = 2

        for node in graph:
            visit(node, [])

    def get(self, metric_id: str) -> Metric:
        for metric in self.metrics:
            if metric.id == metric_id:
                return metric
        raise KeyError(metric_id)


def load_metrics(path: Path) -> MetricsFile:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return MetricsFile.model_validate(data)
