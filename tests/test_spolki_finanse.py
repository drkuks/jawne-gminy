"""Electronic financial statements from RDF (manual mode) on real files."""

from __future__ import annotations

import base64
from decimal import Decimal
from pathlib import Path

import pytest

from jg import esprawozdanie as es
from jg import spolki_finanse as sf
from jg.fetch.archive import Archive
from tests.conftest import FIXTURES

RDF = FIXTURES / "rdf"
ZWIK = (RDF / "zwik-2025-esprawozdanie.xml").read_bytes()
TERMY = (RDF / "termy-2024-esprawozdanie.xml").read_bytes()
MZEC = (RDF / "mzec-2025-esprawozdanie-koperta-dokumentu.xml").read_bytes()


def test_zwik_2025_control_values():
    s = es.parse(ZWIK)
    assert (s.krs, s.rok, s.wariant_rzis, s.metoda_przeplywow) == (
        "0000136724",
        2025,
        "RZiSKalk",
        "PrzeplywyPosr",
    )
    assert s.kontrola == "ok"
    assert s.wartosci["przychody_netto"][0] == Decimal("40122668.01")
    assert s.wartosci["wynik_netto"][0] == Decimal("1803481.99")
    assert s.wartosci["naklady_inwestycyjne"] == (
        Decimal("1891014.21"),
        "RachPrzeplywow/PrzeplywyPosr/B/B_II/B_II_1",
    )


def test_termy_without_cash_flow():
    s = es.parse(TERMY)
    assert s.wariant_rzis == "RZiSPor" and s.metoda_przeplywow is None
    assert s.wartosci["przychody_netto"][0] == Decimal("6261864.73")
    assert s.wartosci["wynik_netto"][0] == Decimal("-822987.55")
    assert "naklady_inwestycyjne" not in s.wartosci


def test_mzec_in_document_envelope():
    s = es.parse(MZEC)
    assert s.krs == "0000101760" and s.rok == 2025
    assert s.wartosci["przychody_netto"][0] == Decimal("17944916.28")


def test_base64_xades_envelope():
    wrapped = (
        b'<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#"><ds:Object>'
        + base64.b64encode(TERMY)
        + b"</ds:Object></ds:Signature>"
    )
    assert es.parse(wrapped).wartosci == es.parse(TERMY).wartosci


def test_balance_violation_is_reported():
    import re

    text = ZWIK.decode("utf-8")
    # First KwotaA after the opening Aktywa element (total assets) changed by 1 zł.
    m = re.search(r"<(\w+:)?Aktywa>\s*<(\w+:)?KwotaA>([\d.]+)<", text)
    assert m
    old = m.group(3)
    new = str(Decimal(old) + 1)
    bad = (text[: m.start(3)] + new + text[m.end(3) :]).encode("utf-8")
    assert es.parse(bad).kontrola.startswith("bilans")


def test_not_xml():
    with pytest.raises(es.StatementError):
        es.parse(b"nie xml")


def test_rows_from_manual_archive(tmp_path):
    pliki = _katalog(tmp_path, {"0000136724/2025/sf.xml": ZWIK, "0000424430/sf.xml.XAdES": TERMY})
    rows = sf.wiersze(pliki, ["0000136724", "0000424430"], [2024, 2025])
    by = {(r.krs, r.rok, r.kod): r for r in rows}
    z = by[("0000136724", 2025, "naklady_inwestycyjne")]
    assert z.wartosc == Decimal("1891014.21") and z.status == "ok" and len(z.zrodlo_sha256) == 64
    assert by[("0000424430", 2024, "naklady_inwestycyjne")].status == (
        "brak danych: brak rachunku przepływów w sprawozdaniu"
    )
    assert by[("0000136724", 2024, "przychody_netto")].status.startswith(
        "brak danych: brak sprawozdania"
    )


def _katalog(tmp_path, files):
    katalog = tmp_path / "rdf"
    for rel, content in files.items():
        (katalog / rel).parent.mkdir(parents=True, exist_ok=True)
        (katalog / rel).write_bytes(content)
    return sf.wczytaj(katalog, Archive(tmp_path / "arch"))


def test_krs_mismatch_is_brak_danych_not_crash(tmp_path):
    pliki = _katalog(tmp_path, {"0000000001/2025/sf.xml": ZWIK, "0000136724/2025/a.xml": ZWIK})
    rows = sf.wiersze(pliki, ["0000000001", "0000136724"], [2025])
    by = {(r.krs, r.kod): r for r in rows}
    assert "KRS w pliku 0000136724 ≠ katalog 0000000001" in by[("0000000001", "wynik_netto")].status
    assert by[("0000136724", "wynik_netto")].status == "ok"


def test_year_directory_mismatch(tmp_path):
    pliki = _katalog(tmp_path, {"0000136724/2024/sf.xml": ZWIK})
    rows = sf.wiersze(pliki, ["0000136724"], [2024, 2025])
    by = {(r.rok, r.kod): r for r in rows}
    assert "rok sprawozdania 2025 ≠ katalog 2024" in by[(2024, "wynik_netto")].status
    assert by[(2025, "wynik_netto")].status.startswith("brak danych: brak sprawozdania")


def test_copies_of_same_report_and_conflicts(tmp_path):
    pliki = _katalog(
        tmp_path,
        {
            "0000136724/2025/sf.xml": ZWIK,
            "0000136724/2025/sf.xml.xades": b"<x>" + ZWIK.split(b"?>", 1)[1] + b"</x>",
            "0000424430/2024/a.XAdES": TERMY,
        },
    )
    rows = sf.wiersze(pliki, ["0000136724", "0000424430"], [2024, 2025])
    by = {(r.krs, r.rok, r.kod): r for r in rows}
    assert by[("0000136724", 2025, "przychody_netto")].status == "ok"
    assert by[("0000424430", 2024, "przychody_netto")].status == "ok"  # .XAdES extension
    import re

    other = re.sub(
        rb"<dtsf:KwotaA>40122668.01</dtsf:KwotaA>", b"<dtsf:KwotaA>1</dtsf:KwotaA>", ZWIK, count=1
    )
    pliki = _katalog(
        tmp_path / "b", {"0000136724/2025/a.xml": ZWIK, "0000136724/2025/b.xml": other}
    )
    rows = sf.wiersze(pliki, ["0000136724"], [2025])
    assert rows[0].status.startswith("brak danych: dwa różne sprawozdania")


def test_unsupported_schema_is_brak_danych(tmp_path):
    pliki = _katalog(tmp_path, {"0000136724/2025/mikro.xml": b"<JednostkaMikro/>"})
    rows = sf.wiersze(pliki, ["0000136724"], [2025])
    assert "JednostkaInna" in rows[0].status


def test_thousands_are_scaled():
    tys = ZWIK.replace(b"SprFinJednostkaInnaWZlotych", b"SprFinJednostkaInnaWTysiacach")
    s = es.parse(tys)
    assert s.mnoznik == 1000
    assert s.wartosci["przychody_netto"][0] == Decimal("40122668.01") * 1000
    assert "tysiącach" in s.wartosci["przychody_netto"][1]


def test_unknown_unit_and_bad_amount():
    with pytest.raises(es.StatementError, match="jednostka"):
        es.parse(ZWIK.replace(b"SprFinJednostkaInnaWZlotych", b"Inny"))
    with pytest.raises(es.StatementError, match="nie jest liczbą"):
        es.parse(
            ZWIK.replace(
                b"<dtsf:KwotaA>40122668.01</dtsf:KwotaA>", b"<dtsf:KwotaA>1 234,5</dtsf:KwotaA>", 1
            )
        )


def test_checks_description_and_download_date(tmp_path):
    pliki = _katalog(tmp_path, {"0000136724/2025/sf.xml": ZWIK})
    (r, *_) = sf.wiersze(pliki, ["0000136724"], [2025])
    assert "aktywa = pasywa" in r.kontrola and "wynik netto" in r.kontrola
    again = sf.wczytaj(tmp_path / "rdf", Archive(tmp_path / "arch"))
    assert again[0].item.fetched_at == pliki[0].item.fetched_at
    manifest = (tmp_path / "arch" / "manifest.jsonl").read_text().splitlines()
    assert len(manifest) == 1


def test_fixture_has_no_signatures():
    for p in Path(RDF).glob("*.xml"):
        assert b"X509Certificate" not in p.read_bytes()
