from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from jg.metrics import MetricsFile, load_metrics

ROOT = Path(__file__).parent.parent

FACT = {
    "id": "dochody_ogolem",
    "name": "Dochody ogółem",
    "kind": "fact",
    "unit": "zł",
    "period": "rok",
    "format": "kwota",
    "entity_kinds": ["gmina"],
    "definition": "Dochody wykonane ogółem.",
    "sources": [{"source_id": "S1", "document": "Rb-27S", "locator": "suma § (kolumna wykonanie)"}],
}
POP = {**FACT, "id": "ludnosc", "unit": "osoba", "format": "liczba", "name": "Ludność"}
DERIVED = {
    **{k: v for k, v in FACT.items() if k != "sources"},
    "id": "dochody_na_mieszkanca",
    "kind": "derived",
    "unit": "zł/mieszk.",
    "formula": "dochody_ogolem / ludnosc",
    "inputs": ["dochody_ogolem", "ludnosc"],
}


def test_repo_metrics_file_is_valid():
    load_metrics(ROOT / "metrics.yaml")


def test_valid_fact_and_derived():
    m = MetricsFile.model_validate({"metrics": [FACT, POP, DERIVED]})
    assert m.get("dochody_na_mieszkanca").formula == "dochody_ogolem / ludnosc"


def test_fact_requires_source():
    with pytest.raises(ValidationError, match="źródła"):
        MetricsFile.model_validate({"metrics": [{**FACT, "sources": []}]})


def test_derived_formula_must_match_inputs():
    bad = {**DERIVED, "formula": "dochody_ogolem / ludnosc / x"}
    with pytest.raises(ValidationError, match="spoza"):
        MetricsFile.model_validate({"metrics": [FACT, POP, bad]})


def test_unknown_input_and_duplicate():
    with pytest.raises(ValidationError, match="nieznane wejście"):
        MetricsFile.model_validate({"metrics": [FACT, DERIVED]})
    with pytest.raises(ValidationError, match="powtórzony"):
        MetricsFile.model_validate({"metrics": [FACT, FACT]})


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        MetricsFile.model_validate({"metrics": [{**FACT, "ranking": 1}]})


def test_formula_functions_and_numbers_allowed():
    pct = {
        **DERIVED,
        "id": "proc",
        "unit": "%",
        "formula": "round(dochody_ogolem / ludnosc * 1e2, 2)",
    }
    MetricsFile.model_validate({"metrics": [FACT, POP, pct]})


def test_cycle_detected():
    a = {**DERIVED, "id": "a", "formula": "b + ludnosc", "inputs": ["b", "ludnosc"]}
    b = {**DERIVED, "id": "b", "formula": "a + ludnosc", "inputs": ["a", "ludnosc"]}
    with pytest.raises(ValidationError, match="cykl"):
        MetricsFile.model_validate({"metrics": [POP, a, b]})
