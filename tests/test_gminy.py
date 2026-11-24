from __future__ import annotations

from collections import Counter

import pytest

from jg.gminy import gminy_z_mf, polacz, read_mf_slownik, read_prg_gml
from tests.conftest import FIXTURES

MF = read_mf_slownik((FIXTURES / "mf" / "slownik_jst_2025.xlsx").read_bytes())
PRG, PRG_SKIPPED = read_prg_gml((FIXTURES / "prg" / "gminy_5.gml").read_bytes(), "abc")


def test_mf_count_2025():
    gminy = gminy_z_mf(MF)
    # MF JST dictionary 2025: GT 1/2/3 = 236 + 1 459 + 718, cities with powiat rights = 66.
    assert Counter(g.typ for g in gminy.values() if not g.mnpp) == {
        "miejska": 236,
        "wiejska": 1459,
        "miejsko-wiejska": 718,
    }
    assert sum(g.mnpp for g in gminy.values()) == 66
    assert len(gminy) == 2479


@pytest.mark.parametrize(
    ("teryt", "nazwa_mf", "typ", "mnpp", "powiat"),
    [
        ("0215011", "OŁAWA", "miejska", False, "0215"),
        ("0215042", "OŁAWA", "wiejska", False, "0215"),
        ("0215033", "JELCZ-LASKOWICE", "miejsko-wiejska", False, "0215"),
        ("0264011", "Wrocław", "miejska", True, "0264"),
        ("1465011", "m. st. Warszawa", "miejska", True, "1465"),
    ],
)
def test_known_codes(teryt, nazwa_mf, typ, mnpp, powiat):
    g = gminy_z_mf(MF)[teryt]
    assert (g.nazwa_mf, g.typ, g.mnpp, g.powiat_teryt, g.woj_teryt) == (
        nazwa_mf,
        typ,
        mnpp,
        powiat,
        powiat[:2],
    )


def test_no_unions():
    assert not any(t.endswith("Z") for t in gminy_z_mf(MF))


def test_prg_points_are_inside_poland_and_near_city():
    by = {p.teryt: p for p in PRG}
    assert set(by) == {"0215011", "0264011", "1465011", "0215042", "0215033"}
    olawa = by["0215011"]
    assert olawa.nazwa == "Oława" and olawa.regon == "931934868"  # REGON from PRG (test file)
    assert 50.9 < olawa.lat < 51.0 and 17.2 < olawa.lon < 17.4
    waw = by["1465011"]
    assert 52.1 < waw.lat < 52.4 and 20.8 < waw.lon < 21.2
    for p in PRG:
        assert 49.0 < p.lat < 54.9 and 14.1 < p.lon < 24.2


def test_join_takes_prg_name_and_reports_missing():
    mf = gminy_z_mf(MF)
    gminy, rozb = polacz(mf, PRG)
    by = {g.teryt: g for g in gminy}
    assert by["0215011"].nazwa == "Oława" and by["0215011"].lat is not None
    assert by["1465011"].nazwa == "Warszawa"
    assert by["0217012"].lat is None and by["0217012"].uwagi
    assert not [r for r in rozb if r.startswith("nazwa:")]
    assert sum(r.startswith("brak w PRG") for r in rozb) == 2479 - 5


def test_type_change_matched_by_prefix():
    from jg.gminy import Gmina, PrgGmina

    mf = {
        "0601052": Gmina(
            "0601052", "JANÓW PODLASKI", "wiejska", False, "0601", "06", "JANÓW PODLASKI"
        )
    }
    prg = [PrgGmina("0601053", "Janów Podlaski", None, 52.2, 23.2)]
    (g,), rozb = polacz(mf, prg)
    assert g.teryt == "0601052" and g.typ == "wiejska" and g.lat == 52.2
    assert "0601053" in g.uwagi[0]
    assert rozb == ["zmiana typu: MF 0601052 / PRG 0601053 Janów Podlaski"]


def test_renamed_gmina_keeps_mf_name_note():
    from jg.gminy import Gmina, PrgGmina

    mf = {
        "2812052": Gmina("2812052", "x", "wiejska", False, "2812", "28", "NOWE MIASTO LUBAWSKIE"),
        "3025032": Gmina(
            "3025032", "x", "miejsko-wiejska", False, "3025", "30", "NOWE MIASTO n. WARTĄ"
        ),
    }
    prg = [
        PrgGmina("2812052", "Bratian", None, 53.4, 19.6),
        PrgGmina("3025032", "Nowe Miasto nad Wartą", None, 52.1, 17.4),
    ]
    gminy, _ = polacz(mf, prg)
    by = {g.teryt: g for g in gminy}
    assert by["2812052"].uwagi == ["nazwa w Słowniku JST MF: NOWE MIASTO LUBAWSKIE"]
    assert by["3025032"].uwagi == []


def test_prg_fixture_complete():
    assert PRG_SKIPPED == [] and all(p.zrodlo_sha256 == "abc" for p in PRG)


def test_wfs_exception_is_error_not_empty_page():
    from jg.gminy import SourceFormatError

    report = (
        b'<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1">'
        b"<ows:Exception><ows:ExceptionText>blad</ows:ExceptionText></ows:Exception>"
        b"</ows:ExceptionReport>"
    )
    with pytest.raises(SourceFormatError, match="ExceptionReport"):
        read_prg_gml(report)


def test_hole_moves_point_out_of_town():
    from shapely.geometry import Polygon

    from jg.gminy import punkt

    # Rural gmina shaped like a ring around a town: the centroid falls in the hole.
    outer = [(0, 0), (0, 10000), (10000, 10000), (10000, 0), (0, 0)]
    hole = [(3000, 3000), (3000, 7000), (7000, 7000), (7000, 3000), (3000, 3000)]
    ring = Polygon(
        [(500000 + x, 400000 + y) for x, y in outer], [[(500000 + x, 400000 + y) for x, y in hole]]
    )
    full = Polygon([(500000 + x, 400000 + y) for x, y in outer])
    assert punkt([ring]) != punkt([full])


def test_join_does_not_mutate_input_and_claims_once():
    from jg.gminy import Gmina, PrgGmina

    mf = {
        "0601052": Gmina("0601052", "A", "wiejska", False, "0601", "06", "A"),
        "0601054": Gmina("0601054", "B", "wiejska", False, "0601", "06", "B"),
    }
    prg = [PrgGmina("0601053", "A", None, 52.2, 23.2)]
    (a, b), rozb = polacz(mf, prg)
    assert mf["0601052"].lat is None and mf["0601052"].uwagi == []
    assert sum(g.lat is not None for g in (a, b)) == 1
    assert any(r.startswith("brak w PRG") for r in rozb)


def test_float_codes_in_xlsx():
    import io

    import openpyxl

    from jg.gminy import read_mf_slownik

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Nazwa", "WK", "PK", "GK", "GT", "PT", "Typ"])
    ws.append(["X", 2.0, 15.0, 1.0, 1.0, 0.0, "Gmina"])
    buf = io.BytesIO()
    wb.save(buf)
    (row,) = read_mf_slownik(buf.getvalue())
    assert (row.wk, row.pk, row.gk, row.gt, row.pt) == ("02", "15", "01", "1", "0")
