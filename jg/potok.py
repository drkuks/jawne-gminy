"""Multi-year pipeline: MF file discovery, fetching, budget, liabilities, population,
derived metrics, combined file and run report.

Each year goes through the same steps as the individual commands (`jg budzet`, `jg rbz`,
`jg wskazniki`). The annual value comes only from that year's "po IV kwartałach" (full-year)
file (period checked in every file). Run report: new documents, number of values and of
"brak danych" by reason, fetch errors.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from jg import bdl, mf_rb, rb_z, wskazniki
from jg import budzet as b
from jg import mf_odkrywanie as odk
from jg.fetch import HttpClient
from jg.fetch.archive import ArchivedFile
from jg.metrics import MetricsFile


class PotokError(Exception):
    pass


@dataclass
class Raport:
    start: str
    lata: list[int]
    wartosci: list[b.Wartosc] = field(default_factory=list)


class PotokHttpError(PotokError):
    """Server or network error (status ≥ 500, no response): the run must stop rather than
    fall back to an earlier year."""


def _get_ok(client: HttpClient, url: str, opis: str):
    r = client.get(url)
    if r.ok:
        return r
    if r.status is None or r.status >= 500 or r.status == 429:
        raise PotokHttpError(f"{opis}: {url} kod {r.status} {getattr(r, 'error', '') or ''}")
    raise PotokError(f"{opis}: {url} kod {r.status}")


def strony_lat(client: HttpClient) -> dict[int, str]:
    return odk.year_pages(_get_ok(client, odk.INDEX_URL, "strona MF").text)


def _baza_roku(client: HttpClient, strony: dict[int, str], rok: int) -> tuple[str, str]:
    """(URL, HTML) of the year's "Bazy danych" page."""
    if rok not in strony:
        raise PotokError(f"brak strony roku {rok} na {odk.INDEX_URL}")
    strona = _get_ok(client, strony[rok], f"{rok}: strona roku")
    db_url = odk.database_page(strona.text, strony[rok])
    if db_url is None:
        raise PotokError(f"{rok}: brak odnośnika „Bazy danych” na {strony[rok]}")
    return db_url, _get_ok(client, db_url, f"{rok}: Bazy danych").text


def odkryj(
    client: HttpClient, lata: list[int], strony: dict[int, str] | None = None
) -> dict[int, dict[str, odk.Attachment]]:
    """URLs of the full-year files (Rb-27S, Rb-28S, Rb-NDS, Rb-Z) for each year."""
    strony = strony if strony is not None else strony_lat(client)
    out = {}
    for rok in lata:
        db_url, html = _baza_roku(client, strony, rok)
        out[rok] = odk.roczne(odk.attachments(html, db_url), rok)
    return out


KWARTAL_W_NAZWIE = re.compile(r"\d\s*kw\b|_\dkw_", re.I)


def wybierz_slownik(items: list[odk.Attachment], rok: int) -> str:
    """Annual Słownik JST in XLSX: no quarter number in the file name ("…_3kw_2025.xlsx")."""
    for a in items:
        t = a.title.lower()
        if (
            a.kind == "Slownik"
            and a.year == rok
            and ".xlsx" in t
            and not KWARTAL_W_NAZWIE.search(t)
        ):
            return a.url
    raise PotokError(f"{rok}: brak rocznego Słownika JST w XLSX (starsze lata: ZIP/XLS)")


def slownik_url(client: HttpClient, rok: int) -> str:
    db_url, html = _baza_roku(client, strony_lat(client), rok)
    return wybierz_slownik(odk.attachments(html, db_url), rok)


def ostatni_rok(client: HttpClient, od: int) -> int:
    """Latest year (≥ `od`) for which MF has published the full set of full-year files.
    A year is skipped only when files or the page are missing (4xx); a server or network
    error stops the run (no silent fallback to an older year)."""
    strony = strony_lat(client)
    for rok in sorted((r for r in strony if r >= od), reverse=True):
        try:
            odkryj(client, [rok], strony)
        except PotokHttpError:
            raise
        except (PotokError, odk.DiscoveryError):
            continue
        return rok
    raise PotokError(f"brak roku od {od} z kompletem plików po IV kwartałach")


def sprawdz_okres(nazwa: str, rok_pliku: int | None, kwartal: str | None, rok: int) -> None:
    if (rok_pliku, kwartal) != (rok, "4"):
        raise PotokError(f"{nazwa}: plik za {rok_pliku}, kw. {kwartal}; oczekiwano {rok}, IV kw.")


def rok_budzetu(
    rok: int, teryty: list[str], pliki: dict[str, ArchivedFile], dane: Path
) -> list[b.Wartosc]:
    """Budget (6 metrics cross-checked with Rb-NDS), działy and liabilities for one year."""
    rb27 = mf_rb.sumy_rb(pliki["Rb27S"].path, "Rb27S")
    rb28 = mf_rb.sumy_rb(pliki["Rb28S"].path, "Rb28S")
    nds = mf_rb.rb_nds(pliki["RbNDS"].path)
    z = rb_z.rb_z(pliki["RbZ"].path)
    for nazwa, p in (("Rb-27S", rb27), ("Rb-28S", rb28), ("Rb-NDS", nds), ("Rb-Z", z)):
        sprawdz_okres(nazwa, p.rok, p.kwartal, rok)
    rows = b.wartosci(rok, teryty, pliki, rb27, rb28, nds.gminy)
    b.zapisz(dane / f"budzet_{rok}.csv", rows)
    b.zapisz_dzialy(
        dane / f"budzet_dzialy_{rok}.csv", rok, teryty, pliki, rb27, rb28, b.status_ogolem(rows)
    )
    zob = wskazniki.wartosci_rbz(rok, teryty, z, pliki["RbZ"])
    b.zapisz(dane / f"zobowiazania_{rok}.csv", zob)
    return rows + zob


def ludnosc(client: HttpClient, teryty: list[str], lata: list[int], key: str | None):
    headers = {bdl.KEY_HEADER: key} if key else None
    client.limiter.set_interval(bdl.BDL_HOST, bdl.BDL_INTERVAL_S)
    dane, strony = [], []
    page, pages = 0, 1
    while page < pages:
        url = bdl.url_by_variable(bdl.ZMIENNA_LUDNOSC, lata, page)
        src = client.fetch_to_archive(url, headers=headers, note="BDL 72305")
        data = json.loads(src.read_bytes())
        pages = bdl.total_pages(data)
        dane.extend(bdl.parse_page(data, len(strony)))
        strony.append(src)
        page += 1
    return wskazniki.wartosci_ludnosc(lata, teryty, dane, strony), strony


def zbiorczy(path: Path, rows: list[b.Wartosc]) -> None:
    """All values (facts and derived metrics) for all years in one file."""
    b.zapisz(path, sorted(rows, key=lambda r: (r.teryt, r.rok, r.kod)))


def nowe_dokumenty(manifest: Path, od: str, przed: set[str]) -> list[dict]:
    """Archive manifest entries added since `od` whose hash was absent before the run."""
    if not manifest.is_file():
        return []
    out = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec["fetched_at"] >= od and rec["sha256"] not in przed:
            out.append(rec)
    return out


def bledy_pobierania(log: Path, od: str) -> list[str]:
    """URLs whose last request in the run failed. Redirects (3xx) and failed attempts later
    followed by 200/304 are not errors."""
    if not log.is_file():
        return []
    ostatnie: dict[str, dict] = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec["ts"] < od:
            continue
        status = rec.get("status")
        if status is not None and 300 <= status < 400 and not rec.get("error"):
            continue
        ostatnie[rec["url"]] = rec
    return [
        f"{r['ts']} {url}: {r.get('status')} {r.get('error') or ''}".strip()
        for url, r in ostatnie.items()
        if r.get("error") or r.get("status") not in (200, 304)
    ]


def zapisz_raport(path: Path, raport: Raport, nowe: list[dict], bledy: list[str]) -> None:
    statusy = Counter(
        "ok" if r.status == "ok" else r.status.split(":", 1)[1].strip().split(" (")[0]
        for r in raport.wartosci
    )
    przyczyny = Counter(
        (r.kod, r.status.split(" (")[0]) for r in raport.wartosci if r.status != "ok"
    )
    lines = [
        "# Raport z przebiegu potoku",
        "",
        f"Start: {raport.start}. Lata: {raport.lata[0]}–{raport.lata[-1]}.",
        "",
        f"## Nowe dokumenty ({len(nowe)})",
        "",
        *[
            f"- {d['source_id']} {d.get('note') or ''}: {d['url']} (sha256 {d['sha256'][:12]}…)"
            for d in nowe
        ],
        "",
        f"## Wartości ({len(raport.wartosci)})",
        "",
        "| Status | Liczba |",
        "|---|---:|",
        *[f"| {k} | {v} |" for k, v in statusy.most_common()],
        "",
        "## Brak danych według wskaźnika i przyczyny",
        "",
        "| Wskaźnik | Przyczyna | Liczba |",
        "|---|---|---:|",
        *[f"| {k} | {p} | {v} |" for (k, p), v in przyczyny.most_common()],
        "",
        f"## Błędy pobierania ({len(bledy)})",
        "",
        *[f"- {e}" for e in bledy],
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def pochodne_lat(
    metrics: MetricsFile, lata: list[int], teryty: list[str], fakty: list[b.Wartosc], dane: Path
) -> list[b.Wartosc]:
    out = []
    for rok in lata:
        rows = wskazniki.pochodne(metrics, rok, teryty, fakty)
        b.zapisz(dane / f"wskazniki_{rok}.csv", rows)
        out.extend(rows)
    return out
