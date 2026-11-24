from __future__ import annotations

from decimal import Decimal

import pytest

from jg import format as f

NB = " "


@pytest.mark.parametrize(
    ("value", "decimals", "text"),
    [
        (1891014.21, 2, f"1{NB}891{NB}014,21"),
        (4999, 0, "4999"),
        (20000, 0, f"20{NB}000"),
        (-1234567, 0, f"−1{NB}234{NB}567"),
        (None, 0, "brak danych"),
    ],
)
def test_number(value, decimals, text):
    assert f.number(value, decimals) == text


def test_amounts_and_percent():
    assert f.pln(Decimal("66393081.00")) == f"66{NB}393{NB}081,00{NB}zł"
    assert f.rounded(Decimal("1891014.21")) == f"1,89{NB}mln{NB}zł"
    assert f.rounded(Decimal("8232304918.58")) == f"8,23{NB}mld{NB}zł"
    assert f.percent(Decimal("84.33")) == "84%" and f.percent(Decimal("0.83")) == "0,8%"
    assert f.day("2026-03-26") == "26.03.2026"
    assert f.plural(5, "gmina", "gminy", "gmin") == "gmin"
    assert f.plural(22, "gmina", "gminy", "gmin") == "gminy"
