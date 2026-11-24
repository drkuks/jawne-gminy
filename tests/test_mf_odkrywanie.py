"""MF file discovery on saved gov.pl pages (copies from okiemnaolawe, 30–31.10.2026)."""

from __future__ import annotations

import pytest

from jg import mf_odkrywanie as m
from jg.teryt import kod_w_roku
from tests.conftest import FIXTURES

STRONY = FIXTURES / "mf" / "strony"


def test_year_pages():
    years = m.year_pages((STRONY / "sprawozdania-budzetowe.html").read_text())
    assert years[2019] == "https://www.gov.pl/web/finanse/2019-r-sprawozdania-budzetowe"
    assert years[2020] == "https://www.gov.pl/web/finanse/2020-r11"
    assert years[2025] == "https://www.gov.pl/web/finanse/2025-r3"


def test_database_page_2019():
    url = m.database_page((STRONY / "2019-r.html").read_text(), "https://www.gov.pl/web/finanse/x")
    assert url == "https://www.gov.pl/web/finanse/bazy-danych2"


def test_attachments_2019():
    items = m.attachments((STRONY / "bazy-danych2-2019.html").read_text(), "https://www.gov.pl/")
    roczne = m.roczne(items, 2019)
    assert set(roczne) == {"Rb27S", "Rb28S", "RbNDS", "RbZ"}
    assert "Dochody_IVkw2019" in roczne["Rb27S"].title
    assert "Finans_IVkw2019" in roczne["RbNDS"].title
    assert all(a.url.startswith("https://www.gov.pl/attachment/") for a in roczne.values())


def test_quarterly_files_skipped():
    assert m.classify("Dochody za III kwartał 2025 r. Rb27S.zip", "u") is None
    assert m.classify("Dochody Grudzień 2025 r. Rb27S.zip", "u") is None
    assert m.classify("Dochody po IV kwartałach 2025 r. Rb27S.zip", "u").kind == "Rb27S"


def test_missing_report_raises():
    with pytest.raises(m.DiscoveryError, match="brak plików"):
        m.roczne([m.Attachment("u", "Rb27S", 2025, "t")], 2025)


@pytest.mark.parametrize(
    ("teryt", "w_pliku", "slownik", "expected"),
    [
        ("0215011", {"0215011"}, {"0215011"}, "0215011"),
        ("0408023", {"0408022"}, {"0408023"}, "0408022"),  # type change after the file year
        ("0408023", {"0408022"}, {"0408023", "0408022"}, None),  # code of another gmina
        ("1207132", set(), {"1207132"}, None),  # gmina created later
    ],
)
def test_kod_w_roku(teryt, w_pliku, slownik, expected):
    assert kod_w_roku(teryt, w_pliku, slownik)[0] == expected
