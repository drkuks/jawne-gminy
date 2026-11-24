"""Full KRS extracts from the KRS API (copies from okiemnaolawe, fetched 30.10.2026)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from jg import krs, spolki
from jg.fetch.archive import ArchivedFile
from tests.conftest import FIXTURES

KRS = FIXTURES / "krs"
ZWIK = krs.parse(json.loads((KRS / "OdpisPelny-0000136724.json").read_text()))
MZEC = krs.parse(json.loads((KRS / "OdpisPelny-0000101760.json").read_text()))
SRC = ArchivedFile("sha", Path("x"), "u", "S5", "2026-11-24T00:00:00+01:00", 1)


def test_company_data():
    assert ZWIK.krs == "0000136724"
    assert ZWIK.regon == "932814669" and ZWIK.nip == "9121748498"
    assert ZWIK.kapital == Decimal("75499000.00")
    assert ZWIK.siedziba_gmina == "OŁAWA"


def test_sole_owner_gmina():
    (w,) = krs.obecni(ZWIK)
    assert w.nazwa == "GMINA MIEJSKA W OŁAWIE" and w.regon == "931934845"
    assert w.calosc and w.udzial_proc(ZWIK.kapital) == Decimal(100)


def test_history_of_shareholders():
    by = {w.nazwa: w for w in MZEC.wspolnicy}
    gmina = by["GMINA MIEJSKA W OŁAWIE"]
    assert not gmina.obecny and gmina.do is not None  # left the company
    zwik = by["ZAKŁAD WODOCIĄGÓW I KANALIZACJI SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ W OŁAWIE"]
    assert zwik.obecny and zwik.do is None and zwik.krs == "0000136724"
    assert zwik.udzial_proc(MZEC.kapital) == Decimal("91.80")


def test_organs_only_functions_current():
    assert ZWIK.organy["zarząd"] == ["PREZES ZARZĄDU"]
    assert len(ZWIK.organy["rada nadzorcza"]) == 3
    assert "nazwisko" not in json.dumps(ZWIK.organy, ensure_ascii=False)


def test_filed_statements():
    assert len(ZWIK.sprawozdania) >= 20


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("72 616 UDZIAŁÓW O ŁĄCZNEJ WARTOŚCI 82 464 215,05 ZŁ", Decimal("82464215.05")),
        (
            "471.385 (…) UDZIAŁÓW O ŁĄCZNEJ WYSOKOŚCI 318.184.875,00 ZŁ (TRZYSTA …)",
            Decimal("318184875.00"),
        ),
    ],
)
def test_share_value_formats(text, value):
    m = krs.SHARE_VALUE.search(text)
    assert krs.kwota(m.group(1)) == value


def test_verification_with_grandchild_company():
    kandydaci = [
        spolki.Kandydat("0000136724", "0215011", "x"),
        spolki.Kandydat("0000101760", "0215011", "x"),
    ]
    odpisy = {"0000136724": (ZWIK, SRC), "0000101760": (MZEC, SRC)}
    wyniki = {
        w.spolka.krs: w for w in spolki.weryfikuj(odpisy, kandydaci, {"931934845": "0215011"})
    }
    assert wyniki["0000136724"].komunalna and wyniki["0000136724"].gminy_wspolnicy == {0: "0215011"}
    assert wyniki["0000101760"].komunalna
    assert "spółka komunalna" in wyniki["0000101760"].powod


def test_not_municipal_without_known_regon():
    kandydaci = [spolki.Kandydat("0000136724", "0215011", "x")]
    (w,) = spolki.weryfikuj({"0000136724": (ZWIK, SRC)}, kandydaci, {})
    assert not w.komunalna and "brak wspólnika-gminy" in w.powod


def test_csv_output(tmp_path):
    kandydaci = [
        spolki.Kandydat("0000136724", "0215011", "x"),
        spolki.Kandydat("0000101760", "0215011", "x"),
    ]
    odpisy = {"0000136724": (ZWIK, SRC), "0000101760": (MZEC, SRC)}
    spolki.zapisz(tmp_path, spolki.weryfikuj(odpisy, kandydaci, {"931934845": "0215011"}), "probka")
    import csv

    with (tmp_path / "spolki_wspolnicy.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert all(v != "" for r in rows for v in r.values())
    gmina = next(r for r in rows if r["krs"] == "0000136724")
    assert gmina["wspolnik_teryt"] == "0215011" and gmina["udzial_proc"] == "100"


def test_seed_list():
    zakres, lista = spolki.wczytaj_liste(Path(__file__).parent.parent / "spolki.yaml")
    assert zakres == "probka" and len(lista) == 5
    assert all(len(k.krs) == 10 and len(k.gmina) == 7 for k in lista)


def test_former_holder_share_not_computed_and_teryt_mapped(tmp_path):
    import csv

    kandydaci = [
        spolki.Kandydat("0000136724", "0215011", "x"),
        spolki.Kandydat("0000101760", "0215011", "x"),
    ]
    odpisy = {"0000136724": (ZWIK, SRC), "0000101760": (MZEC, SRC)}
    spolki.zapisz(tmp_path, spolki.weryfikuj(odpisy, kandydaci, {"931934845": "0215011"}), "probka")
    with (tmp_path / "spolki_wspolnicy.csv").open(encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["krs"] == "0000101760"]
    byly = next(r for r in rows if r["wspolnik_nazwa"].startswith("GMINA"))
    assert byly["udzial_proc"].startswith("brak danych: były wspólnik")
    assert byly["wspolnik_teryt"] == "0215011"


@pytest.mark.parametrize(
    ("text", "value"),
    [
        (
            "37.665 UDZIAŁÓW O ŁĄCZNEJ WYSOKOŚCI 37.665.000 (TRZYDZIEŚCI …) ZŁOTYCH",
            Decimal("37665000"),
        ),
        (
            "UDZIAŁÓW PO 1.000 (TYSIĄC ZŁOTYCH) O ŁĄCZNEJ WARTOŚCI UDZIAŁÓW 34.558.000 (…)",
            Decimal("34558000"),
        ),
        (
            "70896 UDZIAŁÓW O ŁĄCZNEJ WARTOŚCI 70.896.000,00 (SIEDEMDZIESIĄT …)",
            Decimal("70896000.00"),
        ),
    ],
)
def test_share_value_more_formats(text, value):
    assert krs.kwota(krs.SHARE_VALUE.search(text).group(1)) == value


def test_all_zwik_share_texts_parsed():
    data = json.loads((KRS / "OdpisPelny-0000136724.json").read_text())
    for holder in data["odpis"]["dane"]["dzial1"]["wspolnicySpzoo"]:
        items = holder["posiadaneUdzialy"]
        for u in items if isinstance(items, list) else [items]:
            assert krs.SHARE_VALUE.search(u["posiadaneUdzialy"]), u


def test_fetch_error_gives_brak_danych_row(tmp_path):
    import csv

    kandydaci = [
        spolki.Kandydat("0000136724", "0215011", "x"),
        spolki.Kandydat("0000000001", "0215011", "y"),
    ]
    wyniki = spolki.weryfikuj(
        {"0000136724": (ZWIK, SRC)},
        kandydaci,
        {"931934845": "0215011"},
        {"0000000001": "pobranie odpisu KRS: kod 404"},
    )
    spolki.zapisz(tmp_path, wyniki, "probka")
    with (tmp_path / "spolki.csv").open(encoding="utf-8") as fh:
        rows = {r["krs"]: r for r in csv.DictReader(fh)}
    assert rows["0000000001"]["komunalna"] == "0"
    assert rows["0000000001"]["powod"] == "brak danych: pobranie odpisu KRS: kod 404"
    assert rows["0000136724"]["komunalna"] == "1"


def test_gmina_mismatch_recorded():
    kandydaci = [spolki.Kandydat("0000136724", "0215033", "x")]
    (w,) = spolki.weryfikuj({"0000136724": (ZWIK, SRC)}, kandydaci, {"931934845": "0215011"})
    assert w.komunalna and w.gmina_ustalona == "0215011"
    assert "≠ wskazana 0215033" in w.powod


def test_single_pozycja_object_and_missing_sections():
    data = json.loads((KRS / "OdpisPelny-0000136724.json").read_text())
    dane = data["odpis"]["dane"]
    wz = dane["dzial3"]["wzmiankiOZlozonychDokumentach"]
    grupa = wz["wzmiankaOZlozeniuRocznegoSprawozdaniaFinansowego"]
    first = grupa[0] if isinstance(grupa, list) else grupa
    pozycje = first.get("pozycja")
    if isinstance(pozycje, list):
        first["pozycja"] = pozycje[0]
    wz["wzmiankaOZlozeniuRocznegoSprawozdaniaFinansowego"] = first
    s = krs.parse(data)
    assert len(s.sprawozdania) == 1
    del dane["dzial2"], dane["dzial3"]
    s = krs.parse(data)
    assert s.organy == {"zarząd": None, "rada nadzorcza": None} and s.sprawozdania is None


def test_holder_struck_by_name_is_former():
    data = json.loads((KRS / "OdpisPelny-0000136724.json").read_text())
    (holder,) = data["odpis"]["dane"]["dzial1"]["wspolnicySpzoo"]
    holder["posiadaneUdzialy"] = []
    for key in ("nazwa", "identyfikator"):
        items = holder[key] if isinstance(holder[key], list) else [holder[key]]
        for item in items:
            item["nrWpisuWykr"] = "5"
        holder[key] = items
    (w,) = krs.parse(data).wspolnicy
    assert not w.obecny and krs.obecni(krs.parse(data)) == []
