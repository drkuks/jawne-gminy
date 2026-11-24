"""Rb-Z, BDL population and derived indicators on real data samples (2025)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from jg import bdl, calc, rb_z, wskazniki
from jg.budzet import Wartosc
from jg.fetch.archive import ArchivedFile
from jg.metrics import load_metrics
from tests.conftest import FIXTURES

ROOT = Path(__file__).parent.parent
RBZ = rb_z.rb_z(FIXTURES / "mf" / "wycinek-RbZ-2025-kw4.zip")
SRC = ArchivedFile("sha-x", Path("x"), "u", "S1", "2026-11-24T00:00:00+01:00", 1)
METRICS = load_metrics(ROOT / "metrics.yaml")


def test_rbz_only_gminy_and_period():
    assert set(RBZ.gminy) == {"0215011", "0215022", "0264011"}
    assert (RBZ.rok, RBZ.kwartal) == (2025, "4")


@pytest.mark.parametrize(
    ("teryt", "ogolem"),
    [
        ("0215011", Decimal("66393081.00")),
        ("0215022", Decimal("17904085.00")),
        ("0264011", Decimal("4521885772.93")),
    ],
)
def test_rbz_values_and_controls(teryt, ogolem):
    z = RBZ.gminy[teryt]
    assert z.ogolem == ogolem
    assert z.niezgodne() == []


def test_rbz_unbalanced_is_brak_danych():
    z = RBZ.gminy["0215011"]
    bad = rb_z.PlikRbZ(
        {"0215011": rb_z.Zobowiazania("0215011", {**z.pola, "E1": Decimal("5")})},
        "RbZ.dbf",
        2025,
        "4",
    )
    (w,) = wskazniki.wartosci_rbz(2025, ["0215011"], bad, SRC)
    assert w.wartosc is None and "Rb-Z (E = E1+E2+E3+E4" in w.status


def test_bdl_teryt_mapping():
    assert bdl.teryt("030210415011") == "0215011"
    assert bdl.teryt("011212001011") == "1201011"
    assert bdl.teryt("abc") is None


def test_bdl_attributes_missing_is_not_zero():
    data = {
        "results": [
            {
                "id": "030210415011",
                "name": "Oława",
                "values": [
                    {"year": "2024", "val": 33086, "attrId": 1},
                    {"year": "2025", "val": 0, "attrId": 0},
                    {"year": "2023", "val": 0, "attrId": 98},
                ],
            }
        ]
    }
    vals = {w.rok: w.wartosc for w in bdl.parse_page(data)}
    assert vals == {2024: 33086, 2025: None, 2023: 0}


def test_bdl_real_page():
    data = json.loads((FIXTURES / "bdl" / "by-variable-72305-2024-2025-p0.json").read_text())
    vals = bdl.parse_page(data)
    assert bdl.total_pages(data) == 40
    bochnia = {(w.teryt, w.rok): w.wartosc for w in vals}
    assert bochnia[("1201011", 2025)] == 28187


def test_bdl_olawa_by_unit_fixture():
    data = json.loads((FIXTURES / "bdl" / "by-unit-olawa-72305.json").read_text())
    values = {int(v["year"]): v["val"] for v in data["results"][0]["values"]}
    assert values[2025] == 32928  # control value in metrics.yaml


def _bdl(teryt, rok, val):
    return bdl.Wartosc(teryt, rok, val, 1, "X", 0)


def test_population_type_change_fallback():
    dane = [_bdl("0408022", 2019, 5000), _bdl("0408023", 2025, 5100)]
    rows = wskazniki.wartosci_ludnosc([2019, 2025], ["0408023"], dane, [SRC])
    by = {r.rok: r for r in rows}
    assert by[2025].wartosc == 5100 and by[2025].status == "ok"
    assert (
        by[2019].wartosc == 5000
        and "kod BDL w 2019: 0408022 (zmiana typu gminy)" in by[2019].kontrola
    )


def test_population_missing_year_is_brak_danych():
    rows = wskazniki.wartosci_ludnosc([2019], ["1207132"], [], [SRC])
    assert rows[0].wartosc is None and rows[0].status.startswith("brak danych")


def _fakt(kod, val, status="ok"):
    return Wartosc("0215011", 2025, kod, val, status, "d", "l", "s", "p", "k")


def test_per_capita_derived():
    fakty = [
        _fakt("dochody_wykonanie", Decimal("249710017.88")),
        _fakt("ludnosc", Decimal(32928)),
    ]
    rows = {r.kod: r for r in wskazniki.pochodne(METRICS, 2025, ["0215011"], fakty)}
    r = rows["dochody_na_mieszkanca"]
    assert r.wartosc == Decimal("7583.5161") and r.status == "ok"
    assert "ludnosc = 32928" in r.kontrola and r.lokalizator == "wzór: dochody_wykonanie / ludnosc"
    # missing input → "brak danych" with a reason, never 0
    assert rows["zobowiazania_na_mieszkanca"].wartosc is None
    assert "zobowiazania_ogolem" in rows["zobowiazania_na_mieszkanca"].status


def test_input_with_brak_danych_status_is_not_used():
    fakty = [_fakt("dochody_wykonanie", None, "brak danych: x"), _fakt("ludnosc", Decimal(1))]
    rows = {r.kod: r for r in wskazniki.pochodne(METRICS, 2025, ["0215011"], fakty)}
    assert rows["dochody_na_mieszkanca"].wartosc is None


def test_calc_rejects_code_and_handles_zero():
    m = METRICS.get("wydatki_majatkowe_wykonanie_proc")
    w = calc.oblicz(
        m, {"wydatki_majatkowe_wykonanie": Decimal(1), "wydatki_majatkowe_plan": Decimal(0)}
    )
    assert w.wartosc is None and "dzielenie przez zero" in w.status
    bad = m.model_copy(update={"formula": "__import__('os')"})
    with pytest.raises(calc.FormulaError):
        calc.oblicz(
            bad, {"wydatki_majatkowe_wykonanie": Decimal(1), "wydatki_majatkowe_plan": Decimal(1)}
        )


def test_rbz_blank_field_is_brak_danych_not_zero():
    z = rb_z.Zobowiazania("0215011", {k: Decimal(0) for k in rb_z.POLA}, ("E",))
    plik = rb_z.PlikRbZ({"0215011": z}, "RbZ.dbf", 2025, "4")
    (w,) = wskazniki.wartosci_rbz(2025, ["0215011"], plik, SRC)
    assert w.wartosc is None and "puste pola E" in w.status


def test_bdl_value_attr_with_null_reason():
    w = bdl.Wartosc("0215011", 2025, None, 1, "Oława", 0, "030210415011")
    assert "bez wartości" in w.przyczyna_braku
    rows = wskazniki.wartosci_ludnosc([2025], ["0215011"], [w], [SRC])
    assert rows[0].status == "brak danych: BDL bez wartości (atrybut 1)"


def test_bdl_locator_has_unit_id():
    w = bdl.Wartosc("0215011", 2025, 32928, 1, "Oława", 0, "030210415011")
    (r,) = wskazniki.wartosci_ludnosc([2025], ["0215011"], [w], [SRC])
    assert "030210415011" in r.lokalizator


def test_derived_keeps_input_sources_and_reason():
    fakty = [
        Wartosc(
            "0215011",
            2025,
            "dochody_wykonanie",
            None,
            "brak danych: niezgodność z Rb-NDS AW",
            "Rb-27S",
            "l",
            "sha27",
            "2026-11-23",
            "k",
        ),
        Wartosc(
            "0215011",
            2025,
            "ludnosc",
            Decimal(32928),
            "ok",
            "BDL",
            "l",
            "shabdl",
            "2026-11-24",
            "k",
        ),
        Wartosc(
            "0215011",
            2025,
            "wydatki_wykonanie",
            Decimal(100),
            "ok",
            "Rb-28S",
            "l",
            "sha28",
            "2026-11-23",
            "k",
        ),
    ]
    rows = {r.kod: r for r in wskazniki.pochodne(METRICS, 2025, ["0215011"], fakty)}
    d = rows["dochody_na_mieszkanca"]
    assert d.wartosc is None and "niezgodność z Rb-NDS AW" in d.status
    w = rows["wydatki_na_mieszkanca"]
    assert w.status == "ok" and w.zrodlo_sha256 == "sha28;shabdl" and w.pobrano == "2026-11-24"


def test_metrics_reject_bad_formulas():
    from pydantic import ValidationError

    from jg.metrics import MetricsFile

    base = METRICS.get("wydatki_majatkowe_wykonanie_proc").model_dump()
    plan = METRICS.get("wydatki_majatkowe_plan").model_dump()
    wyk = METRICS.get("wydatki_majatkowe_wykonanie").model_dump()
    for formula in (
        "round(wydatki_majatkowe_wykonanie / wydatki_majatkowe_plan)",
        "wydatki_majatkowe_wykonanie / wydatki_majatkowe_plan * True",
        "wydatki_majatkowe_wykonanie ** wydatki_majatkowe_plan",
    ):
        with pytest.raises(ValidationError):
            MetricsFile.model_validate({"metrics": [plan, wyk, {**base, "formula": formula}]})


def test_etag_is_quoted_for_conditional_request():
    from jg.fetch.cache import CacheEntry, ConditionalCache

    e = CacheEntry("u", "_UCR_x", None, None, "s", b"")
    assert ConditionalCache.headers_for(e) == {"If-None-Match": '"_UCR_x"'}
    e2 = CacheEntry("u", 'W/"abc"', None, None, "s", b"")
    assert ConditionalCache.headers_for(e2) == {"If-None-Match": 'W/"abc"'}
