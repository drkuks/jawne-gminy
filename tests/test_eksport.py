from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import pytest

from jg.site import eksport
from tests.site_build import FIXTURES, build_site


@pytest.fixture(scope="module")
def dist(tmp_path_factory):
    return build_site(tmp_path_factory.mktemp("site"))


def test_files_exported_without_empty_fields(dist):
    dane = dist / "dane"
    for name in (
        "gminy.csv",
        "wartosci.csv",
        "porownania.csv",
        "spolki.csv",
        "spolki_wspolnicy.csv",
        "spolki_finanse.csv",
    ):
        path = dane / name
        assert path.is_file(), name
        with path.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert rows and all(v != "" for r in rows for v in r.values()), name


def test_per_gmina_file(dist):
    path = dist / "dane" / "gmina" / "0215011.csv"
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert {r["teryt"] for r in rows} == {"0215011"}
    assert {r["rok"] for r in rows} >= {"2019", "2025"}
    dochody = next(r for r in rows if r["rok"] == "2025" and r["kod"] == "dochody_wykonanie")
    assert dochody["wartosc"] == "249710017.88" and len(dochody["zrodlo_sha256"]) == 64


def test_large_files_zipped(tmp_path, monkeypatch):
    monkeypatch.setattr(eksport, "ZIP_OD", 1000)
    data = tmp_path / "data"
    import shutil

    shutil.copytree(FIXTURES / "site", data)
    pliki = {p.nazwa: p for p in eksport.eksportuj(data, tmp_path / "out")}
    assert "wartosci.csv.zip" in pliki and not (tmp_path / "out" / "dane" / "wartosci.csv").exists()
    with zipfile.ZipFile(tmp_path / "out" / "dane" / "wartosci.csv.zip") as zf:
        text = zf.read("wartosci.csv").decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == pliki["wartosci.csv.zip"].wiersze


def test_data_page_lists_files_fields_and_license(dist):
    html = (dist / "dane.html").read_text(encoding="utf-8")
    assert 'href="dane/wartosci.csv"' in html
    assert "zrodlo_sha256" in html and "CC BY 4.0" in html and "EUPL-1.2" in html
    assert "Źródło: GUS, Bank Danych Lokalnych" in html


def test_card_links_to_csv(dist):
    html = (dist / "gmina" / "0215011.html").read_text(encoding="utf-8")
    assert 'href="../dane/gmina/0215011.csv"' in html


def test_licence_files_in_repo():
    root = Path(__file__).parent.parent
    assert "EUROPEAN UNION PUBLIC LICENCE v. 1.2" in (root / "LICENSE").read_text()
    assert "CC BY 4.0" in (root / "LICENSE-DATA.md").read_text(encoding="utf-8")


def test_size_format_and_reproducible_zip(tmp_path, monkeypatch):
    import shutil

    assert eksport.rozmiar(2544) == "2,5 kB" and eksport.rozmiar(10) == "0,1 kB"
    assert eksport.rozmiar(10_740_255) == "10,7 MB"
    monkeypatch.setattr(eksport, "ZIP_OD", 1000)
    shutil.copytree(FIXTURES / "site", tmp_path / "data")
    a = {p.nazwa: p.sha256 for p in eksport.eksportuj(tmp_path / "data", tmp_path / "a")}
    b = {p.nazwa: p.sha256 for p in eksport.eksportuj(tmp_path / "data", tmp_path / "b")}
    assert a == b


def test_dzialy_header_mismatch(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "budzet_dzialy_2024.csv").write_text("teryt,rok\n1,2024\n", encoding="utf-8")
    (data / "budzet_dzialy_2025.csv").write_text("teryt,rok,x\n1,2025,3\n", encoding="utf-8")
    with pytest.raises(eksport.EksportError, match="inne kolumny"):
        eksport._dzialy(data, tmp_path / "out.csv")


def test_gmina_without_values_gets_file(tmp_path):
    import shutil

    shutil.copytree(FIXTURES / "site", tmp_path / "data")
    eksport.eksportuj(tmp_path / "data", tmp_path / "out", {"0215011", "9999999"})
    text = (tmp_path / "out" / "dane" / "gmina" / "9999999.csv").read_text(encoding="utf-8")
    assert "brak danych: brak wartości gminy" in text and ",," not in text


def test_licence_sources_same_on_page_and_file(dist):
    lic = (Path(__file__).parent.parent / "LICENSE-DATA.md").read_text(encoding="utf-8")
    html = (dist / "dane.html").read_text(encoding="utf-8")
    for zrodlo, warunki in eksport.LICENCJA_ZRODLA:
        assert zrodlo in lic and warunki in lic, zrodlo
        assert zrodlo in html
