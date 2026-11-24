from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from jg import porownania as por
from jg.budzet import Wartosc
from jg.metrics import load_metrics

GRUPY = load_metrics(Path(__file__).parent.parent / "metrics.yaml").grupy


def _w(teryt, kod, val, rok=2025, status="ok"):
    return Wartosc(
        teryt, rok, kod, Decimal(val) if val is not None else None, status, "d", "l", "s", "p", "k"
    )


@pytest.mark.parametrize(
    ("lud", "label"),
    [
        (4999, "0–4999"),
        (5000, "5000–9999"),
        (32928, "20 000–49 999"),
        (100000, "100 000 i więcej"),
    ],
)
def test_population_ranges(lud, label):
    assert por.przedzial(Decimal(lud), GRUPY) == label.replace(" ", "\u00a0").replace(
        "\u00a0i\u00a0więcej", " i więcej"
    )


def test_group_label_mnpp_separate():
    nb = "\u00a0"
    mnpp = por.grupa(por.Gmina("0264011", "miejska", True), Decimal(670000), GRUPY)
    assert mnpp == f"miasto na prawach powiatu, ludność 500{nb}000 i więcej"
    olawa = por.grupa(por.Gmina("0215011", "miejska", False), Decimal(32928), GRUPY)
    assert olawa == f"gmina miejska, ludność 20{nb}000–49{nb}999"


def test_statistics_inclusive_quartiles():
    s = por.statystyki([Decimal(x) for x in (1, 2, 3, 4, 5)])
    assert (s.n, s.minimum, s.q1, s.mediana, s.q3, s.maksimum) == (5, 1, 2, 3, 4, 5)


@pytest.mark.parametrize(
    ("value", "opis"),
    [
        (1, "poniżej pierwszego kwartyla"),
        (2.5, "między pierwszym kwartylem a medianą"),
        (3, "równa medianie"),
        (4, "między medianą a trzecim kwartylem"),
        (5, "powyżej trzeciego kwartyla"),
    ],
)
def test_position_words(value, opis):
    s = por.statystyki([Decimal(x) for x in (1, 2, 3, 4, 5)])
    assert por.polozenie(Decimal(str(value)), s) == opis


def test_compare_group_and_brak_danych():
    gminy = [por.Gmina(f"02150{i}1", "miejska", False) for i in range(6)]
    gminy.append(por.Gmina("0215991", "miejska", False))
    vals = []
    for i, g in enumerate(gminy[:6]):
        vals += [_w(g.teryt, "ludnosc", 30000), _w(g.teryt, "x", 100 + i)]
    vals += [_w("0215991", "ludnosc", None, status="brak danych: z")]
    rows = {r.teryt: r for r in por.porownaj(gminy, vals, ["x"], [2025], GRUPY)}
    r = rows["0215001"]
    assert r.status == "ok" and r.stat.n == 6 and r.stat.mediana == Decimal("102.5")
    assert r.roznica_od_mediany == Decimal("-2.5")
    assert rows["0215991"].grupa.startswith("brak danych: brak ludności")


def test_small_group_not_compared():
    gminy = [por.Gmina(f"02150{i}1", "miejska", False) for i in range(3)]
    vals = [v for g in gminy for v in (_w(g.teryt, "ludnosc", 30000), _w(g.teryt, "x", 1))]
    rows = por.porownaj(gminy, vals, ["x"], [2025], GRUPY)
    assert all("grupa ma 3 gmin" in r.status for r in rows)


def test_no_ranking_columns(tmp_path):
    gminy = [por.Gmina(f"02150{i}1", "miejska", False) for i in range(5)]
    vals = [
        v for i, g in enumerate(gminy) for v in (_w(g.teryt, "ludnosc", 30000), _w(g.teryt, "x", i))
    ]
    out = tmp_path / "p.csv"
    por.zapisz(out, por.porownaj(gminy, vals, ["x"], [2025], GRUPY))
    header = out.read_text(encoding="utf-8").splitlines()[0]
    assert "miejsce" not in header and "ranking" not in header


def test_median_not_rounded_before_comparison():
    s = por.statystyki([Decimal("1.0001"), Decimal("1.0002")] * 3)
    assert s.mediana == Decimal("1.00015")
    assert por.polozenie(Decimal("1.0002"), s) != "równa medianie"


def test_ok_status_without_value_is_brak_danych():
    gminy = [por.Gmina(f"02150{i}1", "miejska", False) for i in range(6)]
    vals = [v for g in gminy for v in (_w(g.teryt, "ludnosc", 30000), _w(g.teryt, "x", 1))]
    vals.append(_w("0215001", "x", None))  # status ok, no value (overrides)
    rows = {r.teryt: r for r in por.porownaj(gminy, vals, ["x"], [2025], GRUPY)}
    assert rows["0215001"].status.startswith("brak danych")


def test_value_kept_without_population():
    gminy = [por.Gmina("1207132", "wiejska", False)]
    rows = por.porownaj(gminy, [_w("1207132", "x", 12)], ["x"], [2025], GRUPY)
    assert rows[0].wartosc == 12 and "brak ludności" in rows[0].status


def test_type_from_year_code():
    g = por.Gmina("0408023", "miejsko-wiejska", False)
    w = Wartosc(
        "0408023",
        2019,
        "dochody_wykonanie",
        Decimal(1),
        "ok",
        "d",
        "l",
        "s",
        "p",
        "Rb-NDS AW = 1; kod w plikach MF 2019: 0408022 (zmiana typu gminy)",
    )
    assert por.typ_w_roku(g, w) == "wiejska"
    assert por.typ_w_roku(g, None) == "miejsko-wiejska"


def test_min_n_from_yaml():
    assert GRUPY.min_n == 5
