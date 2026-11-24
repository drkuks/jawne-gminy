from __future__ import annotations

import json
from decimal import Decimal

import pytest

from jg import potok
from jg.budzet import Wartosc


def _w(kod, status="ok", val=Decimal(1)):
    return Wartosc(
        "0215011", 2025, kod, val if status == "ok" else None, status, "d", "l", "s", "p", "k"
    )


def test_period_check():
    potok.sprawdz_okres("Rb-27S", 2025, "4", 2025)
    with pytest.raises(potok.PotokError, match="IV kw"):
        potok.sprawdz_okres("Rb-27S", 2025, "3", 2025)
    with pytest.raises(potok.PotokError):
        potok.sprawdz_okres("Rb-27S", 2024, "4", 2025)


def test_new_documents_and_errors(tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {
                    "sha256": "a",
                    "fetched_at": "2026-11-24T09:00:00+01:00",
                    "url": "u1",
                    "source_id": "S1",
                    "note": "Rb27S",
                },
                {
                    "sha256": "b",
                    "fetched_at": "2026-11-24T10:00:00+01:00",
                    "url": "u2",
                    "source_id": "S1",
                    "note": "Rb28S",
                },
                {
                    "sha256": "a",
                    "fetched_at": "2026-11-24T10:00:01+01:00",
                    "url": "u1",
                    "source_id": "S1",
                    "note": "Rb27S",
                },
            ]
        ),
        encoding="utf-8",
    )
    nowe = potok.nowe_dokumenty(manifest, "2026-11-24T09:30:00+01:00", {"a"})
    assert [d["sha256"] for d in nowe] == ["b"]
    log = tmp_path / "requests.jsonl"
    log.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"ts": "2026-11-24T10:00:00+01:00", "url": "x", "status": 200, "error": None},
                {"ts": "2026-11-24T10:00:05+01:00", "url": "y", "status": 429, "error": None},
                {"ts": "2026-11-24T10:00:09+01:00", "url": "z", "status": None, "error": "Timeout"},
                {"ts": "2026-11-24T10:00:10+01:00", "url": "c", "status": 304, "error": None},
            ]
        ),
        encoding="utf-8",
    )
    bledy = potok.bledy_pobierania(log, "2026-11-24T09:30:00+01:00")
    assert len(bledy) == 2 and "429" in bledy[0] and "Timeout" in bledy[1]


def test_report(tmp_path):
    r = potok.Raport("2026-11-24T10:00:00+01:00", [2019, 2025])
    r.wartosci = [
        _w("a"),
        _w("b", "brak danych: brak gminy w Rb-27S 2019"),
        _w("b", "brak danych: brak gminy w Rb-27S 2019"),
    ]
    out = tmp_path / "r.md"
    potok.zapisz_raport(out, r, [], ["e1"])
    text = out.read_text(encoding="utf-8")
    assert "| ok | 1 |" in text
    assert "| b | brak danych: brak gminy w Rb-27S 2019 | 2 |" in text
    assert "## Błędy pobierania (1)" in text and "Lata: 2019–2025" in text


def test_combined_file_sorted(tmp_path):
    rows = [_w("z"), _w("a")]
    potok.zbiorczy(tmp_path / "w.csv", rows)
    lines = (tmp_path / "w.csv").read_text(encoding="utf-8").splitlines()
    assert lines[1].split(",")[2] == "a"


def test_redirects_and_recovered_retries_are_not_errors(tmp_path):
    log = tmp_path / "requests.jsonl"
    recs = [
        {"ts": "2026-11-24T10:00:00+01:00", "url": "a", "status": 302, "error": None},
        {"ts": "2026-11-24T10:00:01+01:00", "url": "b", "status": 429, "error": None},
        {"ts": "2026-11-24T10:00:10+01:00", "url": "b", "status": 200, "error": None},
        {"ts": "2026-11-24T10:00:20+01:00", "url": "c", "status": 200, "error": None},
        {"ts": "2026-11-24T10:00:30+01:00", "url": "c", "status": 503, "error": None},
    ]
    log.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    bledy = potok.bledy_pobierania(log, "2026-11-24T09:00:00+01:00")
    assert len(bledy) == 1 and " c: 503" in bledy[0]


def test_zip_member_keeps_path(tmp_path):
    import zipfile

    from jg import mf_rb
    from tests.conftest import FIXTURES

    src = FIXTURES / "mf" / "wycinek-RbNDS-2025-kw4.zip"
    with zipfile.ZipFile(src) as zf:
        data = zf.read("RbNDS.dbf")
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("2025/RbNDS.dbf", data)
        zf.writestr("2025/inne.dbf", b"nie dbf")
    assert mf_rb.rb_nds(archive).member == "2025/RbNDS.dbf"


class _Resp:
    def __init__(self, text, ok=True):
        self.text, self.ok, self.status = text, ok, 200 if ok else 404


class _Client:
    """Test client: URL → saved gov.pl page (tests/fixtures/mf/strony)."""

    def __init__(self, pages):
        self.pages = pages

    def get(self, url):
        return _Resp(self.pages[url]) if url in self.pages else _Resp("", ok=False)


def _strony():
    from tests.conftest import FIXTURES

    d = FIXTURES / "mf" / "strony"
    return {
        "https://www.gov.pl/web/finanse/sprawozdania-budzetowe": (
            d / "sprawozdania-budzetowe.html"
        ).read_text(),
        "https://www.gov.pl/web/finanse/2019-r-sprawozdania-budzetowe": (
            d / "2019-r.html"
        ).read_text(),
        "https://www.gov.pl/web/finanse/bazy-danych2": (d / "bazy-danych2-2019.html").read_text(),
    }


def test_latest_complete_year_skips_years_without_files():
    # Saved pages: years 2020–2026 unavailable (404), 2019 with the full set of files.
    assert potok.ostatni_rok(_Client(_strony()), 2019) == 2019
    with pytest.raises(potok.PotokError):
        potok.ostatni_rok(_Client(_strony()), 2020)


def test_slownik_url_annual_xlsx_or_error():
    with pytest.raises(potok.PotokError, match="XLSX"):
        potok.slownik_url(_Client(_strony()), 2019)  # 2019: dictionary inside a ZIP


def test_slownik_annual_2025_from_real_page():
    from jg import mf_odkrywanie as m
    from tests.conftest import FIXTURES

    html = (FIXTURES / "mf" / "strony" / "bazy-danych8-2025.html").read_text()
    url = potok.wybierz_slownik(m.attachments(html, "https://www.gov.pl/"), 2025)
    assert url == "https://www.gov.pl/attachment/737c9fd5-8f45-4424-8f8d-9fd5a2e656fe"
    fake = [
        m.Attachment(
            "u", "Slownik", 2026, "Słownik JST 2026 po IV kwartałach Slownik_JST_2026.xlsx"
        )
    ]
    assert potok.wybierz_slownik(fake, 2026) == "u"


def test_server_error_stops_instead_of_older_year():
    pages = _strony()
    pages["https://www.gov.pl/web/finanse/2025-r3"] = None  # 503 response

    class C(_Client):
        def get(self, url):
            if url == "https://www.gov.pl/web/finanse/2025-r3":
                r = _Resp("", ok=False)
                r.status = 503
                return r
            return super().get(url)

    with pytest.raises(potok.PotokHttpError):
        potok.ostatni_rok(C(pages), 2019)


def test_workflows_structure():
    from pathlib import Path

    import yaml

    root = Path(__file__).parent.parent / ".github" / "workflows"
    dane = yaml.safe_load((root / "data.yml").read_text(encoding="utf-8"))
    on = dane[True] if True in dane else dane["on"]  # PyYAML reads "on" as True
    assert on["schedule"][0]["cron"] == "17 3 * * 1"
    kroki = " ".join(str(s.get("run", "")) for s in dane["jobs"]["przebieg"]["steps"])
    assert "jg przebieg" in kroki and "--bez-finansow-spolek" in kroki
    assert "${{ inputs" not in kroki  # parameters passed only via env
    assert dane["jobs"]["publikacja"]["if"] == "${{ vars.PUBLIKACJA == 'tak' }}"
    testy = yaml.safe_load((root / "tests.yml").read_text(encoding="utf-8"))
    kroki = " ".join(str(s.get("run", "")) for s in testy["jobs"]["pytest"]["steps"])
    assert "playwright install" in kroki and "pytest" in kroki
