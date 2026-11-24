"""Downloadable data export: CSV files in `site/dist/dane/` with field descriptions.

The combined files are copies of the pipeline files (same columns, UTF-8, comma separator, decimal
point, `brak danych` with a reason instead of empty fields). Each gmina gets a separate file with
its values (facts and metrics for all years). Data licence: CC BY 4.0 with attribution of the
primary sources.
"""

from __future__ import annotations

import csv
import hashlib
import shutil
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from jg.budzet import BRAK

PLIKI = (
    ("gminy.csv", "Słownik gmin: TERYT, nazwa, typ, powiat, województwo, punkt na mapie, źródła."),
    (
        "wartosci.csv",
        "Wszystkie wartości budżetu, zobowiązań, ludności i wskaźników, lata 2019–2025.",
    ),
    ("porownania.csv", "Grupy porównawcze: statystyki grupy i położenie gminy dla wskaźników."),
    ("budzet_dzialy.csv", "Plan i wykonanie dochodów i wydatków według działów klasyfikacji."),
    ("spolki.csv", "Spółki komunalne (próbka): dane z KRS, gmina, organy (funkcje)."),
    ("spolki_wspolnicy.csv", "Wspólnicy spółek z udziałem i datami."),
    ("spolki_finanse.csv", "Przychody, wynik netto, nakłady inwestycyjne spółek (RDF)."),
)

# Column descriptions (shared by files with the same layout).
POLA = {
    "teryt": "Kod TERYT gminy (7 znaków: województwo, powiat, gmina, typ).",
    "rok": "Rok budżetowy albo obrotowy.",
    "kod": "Identyfikator wskaźnika z metrics.yaml (definicja na stronie Metodologia).",
    "wartosc": "Wartość (kropka dziesiętna, bez separatora tysięcy) albo „brak danych”.",
    "status": "„ok” albo „brak danych: przyczyna”.",
    "dokument": "Dokument źródłowy (sprawozdanie, odpis, zbiór).",
    "lokalizator": "Pozycja w dokumencie albo wzór wskaźnika pochodnego.",
    "zrodlo_sha256": "SHA-256 pliku źródłowego w archiwum projektu (dla wskaźników pochodnych: "
    "hashe wejść rozdzielone średnikiem).",
    "pobrano": "Data i czas pobrania pliku źródłowego (ISO 8601, czas polski).",
    "kontrola": "Wykonane kontrole (np. porównanie z Rb-NDS) albo wejścia wzoru.",
    "grupa": "Grupa porównawcza (typ gminy i przedział ludności).",
    "n": "Liczba gmin w grupie z wartością wskaźnika.",
    "minimum": "Najniższa wartość w grupie.",
    "q1": "Pierwszy kwartyl (statistics.quantiles, metoda inclusive).",
    "mediana": "Mediana grupy.",
    "q3": "Trzeci kwartyl.",
    "maksimum": "Najwyższa wartość w grupie.",
    "polozenie": "Położenie wartości gminy względem kwartyli grupy (opis słowny).",
    "roznica_od_mediany": "Wartość gminy minus mediana grupy.",
    "krs": "Numer KRS spółki (10 cyfr).",
}


ZIP_OD = 10_000_000  # larger CSV files are published as ZIP archives (hosting file size limit)


class EksportError(Exception):
    pass


# Primary sources and terms: the same list on dane.html and in LICENSE-DATA.md (tested).
LICENCJA_ZRODLA = (
    (
        "Ministerstwo Finansów, sprawozdania budżetowe JST",
        "informacja publiczna; ponowne wykorzystywanie (ustawa z 11.08.2021 o otwartych danych)",
    ),
    ("GUS, Bank Danych Lokalnych", "CC BY 4.0, atrybucja: „Źródło: GUS, Bank Danych Lokalnych”"),
    (
        "GUGiK, Państwowy Rejestr Granic",
        "dane udostępniane bez opłat (Prawo geodezyjne i kartograficzne, art. 40a)",
    ),
    (
        "Ministerstwo Sprawiedliwości, API KRS i Repozytorium Dokumentów Finansowych",
        "otwarte dane (ustawa o KRS, art. 4b)",
    ),
)
ZIP_DATE = (1980, 1, 1, 0, 0, 0)  # fixed ZIP entry date: same file → same SHA-256


@dataclass(frozen=True)
class Plik:
    nazwa: str
    opis: str
    wiersze: int
    kolumny: list[str]
    rozmiar: int
    sha256: str


def rozmiar(n: int) -> str:
    """File size in decimal units: `2,5 kB`, `10,7 MB` (never `0,0`)."""
    from jg.format import number

    if n < 1_000_000:
        return f"{number(max(n, 100) / 1000, 1)}\u00a0kB"
    return f"{number(n / 1_000_000, 1)}\u00a0MB"


def _opis(path: Path, opis: str) -> Plik:
    """File description; a CSV larger than ZIP_OD is replaced by a `<name>.zip` archive
    with a fixed entry date (reproducible SHA-256)."""
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        kolumny = next(reader)
        wiersze = sum(1 for _ in reader)
    if path.stat().st_size > ZIP_OD:
        archiwum = path.with_name(path.name + ".zip")
        info = zipfile.ZipInfo(path.name, date_time=ZIP_DATE)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        with zipfile.ZipFile(archiwum, "w") as zf:
            zf.writestr(info, path.read_bytes(), compresslevel=9)
        path.unlink()
        path = archiwum
    data = path.read_bytes()
    return Plik(path.name, opis, wiersze, kolumny, len(data), hashlib.sha256(data).hexdigest())


def _dzialy(data: Path, out: Path) -> None:
    """Merge `budzet_dzialy_YYYY.csv` files into one; headers must be identical."""
    pliki = sorted(data.glob("budzet_dzialy_*.csv"))
    if not pliki:
        return
    with out.open("w", newline="", encoding="utf-8") as dst:
        w = csv.writer(dst)
        first = None
        for p in pliki:
            with p.open(encoding="utf-8", newline="") as src:
                r = csv.reader(src)
                header = next(r)
                if first is None:
                    first = header
                    w.writerow(header)
                elif header != first:
                    raise EksportError(f"{p.name}: inne kolumny niż {pliki[0].name}")
                w.writerows(r)


def eksportuj(data: Path, out: Path, gminy: set[str] | None = None) -> list[Plik]:
    """Combined files and a file for each gmina in `gminy` (default: those with values). A gmina
    without values gets a file with a `brak danych` row and a reason (its page link never 404s)."""
    dane = out / "dane"
    (dane / "gmina").mkdir(parents=True, exist_ok=True)
    per_gmina: dict[str, list[list[str]]] = defaultdict(list)
    with (data / "wartosci.csv").open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        lata = set()
        for row in reader:
            per_gmina[row[0]].append(row)
            lata.add(row[1])
    zakres = f"{min(lata)}–{max(lata)}" if lata else BRAK
    wynik = []
    for nazwa, opis in PLIKI:
        target = dane / nazwa
        if nazwa == "budzet_dzialy.csv":
            _dzialy(data, target)
        elif (data / nazwa).is_file():
            shutil.copy(data / nazwa, target)
        if target.is_file():
            wynik.append(_opis(target, opis.replace("{lata}", zakres)))
    for teryt in sorted(gminy if gminy is not None else per_gmina):
        rows = per_gmina.get(teryt) or [
            [teryt, BRAK, BRAK, BRAK, f"{BRAK}: brak wartości gminy w data/wartosci.csv"]
            + [BRAK] * (len(header) - 5)
        ]
        with (dane / "gmina" / f"{teryt}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
    return wynik
