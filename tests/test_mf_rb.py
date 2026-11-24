"""Rb-27S / Rb-28S / Rb-NDS parser on samples of real MF files for 2025 (after Q4).

The samples (scripts/wycinek_dbf.py) contain all records of three gminas: Oława (urban,
0215011), Domaniów (rural, 0215022), Wrocław (city with powiat rights, 0264011), plus the
first 20 records of other units (voivodeships, powiats, unions) to test filtering.
Control values from Rb-NDS come from an independent MF report for the same gminas.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from jg import mf_rb
from tests.conftest import FIXTURES

MF = FIXTURES / "mf"
RB27 = mf_rb.sumy_rb(MF / "wycinek-Rb27S-2025-kw4.zip", "Rb27S")
RB28 = mf_rb.sumy_rb(MF / "wycinek-Rb28S-2025-kw4.zip", "Rb28S")
NDS_FILE = mf_rb.rb_nds(MF / "wycinek-RbNDS-2025-kw4.zip")
NDS = NDS_FILE.gminy
GMINY = {"0215011", "0215022", "0264011"}


def test_only_gminy_are_kept():
    assert set(RB27.gminy) == GMINY
    assert set(RB28.gminy) == GMINY
    assert set(NDS) == GMINY


def test_period():
    assert (RB27.rok, RB27.kwartal) == (2025, "4")
    assert (RB28.rok, RB28.kwartal, NDS_FILE.rok, NDS_FILE.kwartal) == (2025, "4", 2025, "4")
    assert RB28.member == "Rb28S.dbf" and NDS_FILE.member == "RbNDS.dbf"


@pytest.mark.parametrize(
    ("teryt", "dochody", "majatkowe"),
    [
        # Rb-NDS 2025 Q4: field AW (revenue executed) and B2W (capital expenditure executed).
        ("0215011", Decimal("249710017.88"), Decimal("66839244.24")),
        ("0215022", Decimal("56127124.43"), Decimal("26774730.99")),
        ("0264011", Decimal("8232304918.58"), Decimal("958773845.06")),
    ],
)
def test_known_values(teryt, dochody, majatkowe):
    assert RB27.gminy[teryt].wykonanie == dochody == NDS[teryt]["AW"]
    assert RB28.gminy[teryt].wykonanie_majatkowe == majatkowe == NDS[teryt]["B2W"]


def test_balancing_passes_for_all_checks():
    checks = mf_rb.bilansuj(RB27.gminy, RB28.gminy, NDS)
    assert len(checks) == 3 * 6
    assert all(c.zgodna for c in checks), [c for c in checks if not c.zgodna]


def test_balancing_detects_difference():
    s = mf_rb.Sumy(plan=Decimal("10"), wykonanie=Decimal("5"))
    nds = {"0000001": {k: Decimal("0") for k in mf_rb.NDS_FIELDS} | {"AW": Decimal("5.02")}}
    checks = {c.nazwa: c for c in mf_rb.bilansuj({"0000001": s}, {}, nds)}
    assert not checks["dochody_wykonanie"].zgodna
    assert not checks["dochody_plan"].zgodna


def test_missing_nds_is_not_balanced():
    (c, *_) = mf_rb.bilansuj({"0215011": RB27.gminy["0215011"]}, {}, {})
    assert c.nds is None and not c.zgodna


def test_dzialy_sum_to_total():
    for s in (*RB27.gminy.values(), *RB28.gminy.values()):
        assert sum(v[1] for v in s.dzialy.values()) == s.wykonanie
        assert sum(v[0] for v in s.dzialy.values()) == s.plan


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("02", "15", "01", "1", "0"), "0215011"),
        (("02", "15", "02", "2", "0"), "0215022"),
        (("02", "64", "00", "0", "2"), "0264011"),
        (("02", "15", "00", "0", "1"), None),  # powiat
        (("02", "00", "00", "0", "0"), None),  # voivodeship
        (("02", "15", "01", "Z", "8"), None),  # union of JST
    ],
)
def test_gmina_teryt(args, expected):
    from jg.teryt import gmina_teryt

    assert gmina_teryt(*args) == expected


def test_wrong_archive_raises(tmp_path):
    import zipfile

    bad = tmp_path / "x.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("inne.txt", "x")
    with pytest.raises(mf_rb.MfFormatError):
        mf_rb.sumy_rb(bad, "Rb27S")


def test_rok_z_pola():
    assert mf_rb.rok_z_pola("25") == 2025 and mf_rb.rok_z_pola("2025") == 2025
    with pytest.raises(mf_rb.MfFormatError):
        mf_rb.rok_z_pola("5")


def test_mixed_periods_rejected():
    okres = mf_rb.Okres()
    okres.check("25", "4")
    with pytest.raises(mf_rb.MfFormatError, match="różne okresy"):
        okres.check("25", "3")


def test_same_code_from_two_units_rejected():
    klucze = mf_rb.Klucze("Rb27S")
    klucze.check("0264011", ("02", "64", "00", "0", "2"))
    with pytest.raises(mf_rb.MfFormatError, match="dwóch jednostek"):
        klucze.check("0264011", ("02", "64", "01", "1", "0"))


def test_bad_number_has_context():
    with pytest.raises(mf_rb.MfFormatError, match="R4"):
        mf_rb.num("1,5", "Rb27S rekord 7 R4")


def test_7z_archive(tmp_path):
    import zipfile

    import py7zr

    with zipfile.ZipFile(MF / "wycinek-RbNDS-2025-kw4.zip") as zf:
        zf.extract("RbNDS.dbf", tmp_path)
    archive = tmp_path / "x.7z"
    with py7zr.SevenZipFile(archive, "w") as z:
        z.write(tmp_path / "RbNDS.dbf", "RbNDS.dbf")
    n = mf_rb.rb_nds(archive)
    assert n.gminy == NDS and n.member == "RbNDS.dbf"


def test_not_an_archive(tmp_path):
    bad = tmp_path / "x.bin"
    bad.write_bytes(b"nie archiwum")
    with pytest.raises(mf_rb.MfFormatError, match="ZIP ani 7z"):
        mf_rb.rb_nds(bad)
