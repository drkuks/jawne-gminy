from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from jg import budzet, mf_rb
from jg.fetch.archive import ArchivedFile
from tests.test_mf_rb import NDS, RB27, RB28


def _plik(rep: str) -> ArchivedFile:
    return ArchivedFile(f"sha-{rep}", Path("x"), "u", "S1", "2026-11-23T00:00:00+01:00", 1)


PLIKI = {r: _plik(r) for r in ("Rb27S", "Rb28S", "RbNDS")}


def test_values_with_source_and_control():
    rows = budzet.wartosci(2025, ["0215011"], PLIKI, RB27, RB28, NDS)
    by = {r.kod: r for r in rows}
    assert set(by) == {k.kod for k in mf_rb.KONTROLE}
    r = by["dochody_wykonanie"]
    assert r.wartosc == Decimal("249710017.88") and r.status == "ok"
    assert r.zrodlo_sha256 == "sha-Rb27S" and r.kontrola == "Rb-NDS AW = 249710017.88"
    assert by["wydatki_majatkowe_plan"].lokalizator == "suma R1 wierszy GRUPA 16xx"


def test_missing_gmina_is_brak_danych_with_reason():
    rows = budzet.wartosci(2025, ["9999999"], PLIKI, RB27, RB28, NDS)
    assert all(r.wartosc is None and r.status.startswith("brak danych: brak gminy") for r in rows)


def test_unbalanced_value_is_not_published():
    nds = {k: dict(v) for k, v in NDS.items()}
    nds["0215011"]["AW"] += Decimal("1")
    rows = budzet.wartosci(2025, ["0215011"], PLIKI, RB27, RB28, nds)
    r = next(r for r in rows if r.kod == "dochody_wykonanie")
    assert r.wartosc is None and "niezgodność z Rb-NDS AW" in r.status


def test_csv_never_empty(tmp_path):
    rows = budzet.wartosci(2025, ["0215011", "9999999"], PLIKI, RB27, RB28, NDS)
    out = tmp_path / "b.csv"
    budzet.zapisz(out, rows)
    with out.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            assert all(v != "" for v in row.values())


def test_dzialy_file_has_source_and_brak_danych(tmp_path):
    gminy = ["0215011", "9999999"]
    rows = budzet.wartosci(2025, gminy, PLIKI, RB27, RB28, NDS)
    out = tmp_path / "d.csv"
    budzet.zapisz_dzialy(out, 2025, gminy, PLIKI, RB27, RB28, budzet.status_ogolem(rows))
    with out.open(encoding="utf-8") as fh:
        all_rows = list(csv.DictReader(fh))
    assert all(v != "" for r in all_rows for v in r.values())
    olawa = [r for r in all_rows if r["teryt"] == "0215011" and r["sprawozdanie"] == "Rb-28S"]
    assert sum(Decimal(r["wykonanie"]) for r in olawa) == RB28.gminy["0215011"].wykonanie
    assert olawa[0]["zrodlo_sha256"] == "sha-Rb28S" and olawa[0]["status"] == "ok"
    assert "dział" in olawa[0]["lokalizator"]
    brak = [r for r in all_rows if r["teryt"] == "9999999"]
    assert len(brak) == 2 and all(r["status"].startswith("brak danych") for r in brak)
