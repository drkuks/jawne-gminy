"""Company finances from RDF e-sprawozdania (e-filed financial statements) in manual mode.

Files downloaded by hand from https://rdf-przegladarka.ms.gov.pl/ (an anti-bot firewall rules
out automated fetching) are stored in `archive/manual/rdf/<KRS>/<rok>/`. Each file goes into the
SHA-256 archive (address `manual:rdf/<KRS>/<rok>/<plik>`, fetch date = file write date), is
parsed and saved with its source. A problem with one file (different schema, KRS or year not
matching the directory, two different statements for a year) yields `brak danych` with the
reason for that company and year, without stopping the run.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from jg import esprawozdanie as es
from jg.budzet import BRAK
from jg.fetch.archive import Archive, ArchivedFile
from jg.fetch.reqlog import TZ

KODY = ("przychody_netto", "wynik_netto", "naklady_inwestycyjne")


@dataclass(frozen=True)
class Plik:
    krs_katalog: str
    rok_katalog: int | None
    item: ArchivedFile
    sprawozdanie: es.Sprawozdanie | None
    blad: str | None


@dataclass(frozen=True)
class Wiersz:
    krs: str
    rok: int
    kod: str
    wartosc: Decimal | None
    status: str
    dokument: str
    lokalizator: str
    zrodlo_sha256: str
    pobrano: str
    kontrola: str


def _czy_sprawozdanie(name: str) -> bool:
    name = name.lower()
    return name.endswith(".xml") or name.endswith(".xades")


def wczytaj(katalog: Path, archive: Archive) -> list[Plik]:
    """XML files (also in a XAdES envelope) from `katalog/<KRS>/[<rok>/]*`."""
    out = []
    for path in sorted(katalog.rglob("*")):
        if not path.is_file() or not _czy_sprawozdanie(path.name):
            continue
        parts = path.relative_to(katalog).parts
        if len(parts) < 2:
            continue  # file outside the company directory
        rok_kat = int(parts[1]) if len(parts) > 2 and parts[1].isdigit() else None
        mtime = datetime.fromtimestamp(path.stat().st_mtime, TZ).isoformat(timespec="seconds")
        item = archive.store(
            path.read_bytes(),
            url=f"manual:rdf/{path.relative_to(katalog).as_posix()}",
            source_id="S6",
            content_type="application/xml",
            note="RDF tryb ręczny (data = zapis pliku)",
            fetched_at=mtime,
        )
        try:
            s, blad = es.parse(item.read_bytes()), None
        except es.StatementError as exc:
            s, blad = None, str(exc)
        if s is not None and s.krs != parts[0]:
            s, blad = None, f"KRS w pliku {s.krs} ≠ katalog {parts[0]}"
        elif s is not None and rok_kat is not None and s.rok != rok_kat:
            s, blad = None, f"rok sprawozdania {s.rok} ≠ katalog {rok_kat}"
        out.append(Plik(parts[0], rok_kat, item, s, blad))
    return out


def _wybierz(pliki: list[Plik]) -> tuple[Plik | None, str | None]:
    """One statement per company and year. Copies with the same values (e.g. .xml
    and .xml.xades) are one statement; differing values = conflict (brak danych)."""
    dobre = [p for p in pliki if p.sprawozdanie is not None]
    if not dobre:
        return None, pliki[0].blad if pliki else None
    pierwszy = dobre[0]
    for p in dobre[1:]:
        if p.sprawozdanie.wartosci != pierwszy.sprawozdanie.wartosci:
            nazwy = ", ".join(x.item.url for x in dobre)
            return None, f"dwa różne sprawozdania za rok ({nazwy})"
    return pierwszy, None


def wiersze(pliki: list[Plik], spolki: list[str], lata: list[int]) -> list[Wiersz]:
    grupy: dict[tuple[str, int], list[Plik]] = {}
    for p in pliki:
        rok = p.sprawozdanie.rok if p.sprawozdanie else p.rok_katalog
        if rok is not None:
            grupy.setdefault((p.krs_katalog, rok), []).append(p)
    out = []
    for krs in spolki:
        for rok in lata:
            wybrany, blad = _wybierz(grupy.get((krs, rok), []))
            for kod in KODY:
                if wybrany is None:
                    przyczyna = blad or "brak sprawozdania w archiwum ręcznym RDF"
                    out.append(
                        Wiersz(
                            krs,
                            rok,
                            kod,
                            None,
                            f"{BRAK}: {przyczyna}",
                            "RDF",
                            BRAK,
                            BRAK,
                            BRAK,
                            BRAK,
                        )
                    )
                    continue
                s, item = wybrany.sprawozdanie, wybrany.item
                dokument = (
                    f"e-sprawozdanie finansowe {s.okres_od.isoformat()}–"
                    f"{s.okres_do.isoformat()} (RDF, tryb ręczny)"
                )
                wartosc, lok = s.wartosci.get(kod, (None, BRAK))
                if s.kontrola != "ok":
                    status, wartosc = f"{BRAK}: niezgodność w sprawozdaniu ({s.kontrola})", None
                elif kod not in s.wartosci:
                    sekcja = "rachunku przepływów" if kod == "naklady_inwestycyjne" else "RZiS"
                    status = f"{BRAK}: brak {sekcja} w sprawozdaniu"
                elif wartosc is None:
                    status = f"{BRAK}: pusta pozycja {lok}"
                else:
                    status = "ok"
                out.append(
                    Wiersz(
                        krs,
                        rok,
                        kod,
                        wartosc,
                        status,
                        dokument,
                        lok,
                        item.sha256,
                        item.fetched_at,
                        s.sprawdzono or BRAK,
                    )
                )
    return out


def zapisz(path: Path, rows: list[Wiersz]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "krs",
                "rok",
                "kod",
                "wartosc",
                "status",
                "dokument",
                "lokalizator",
                "zrodlo_sha256",
                "pobrano",
                "kontrola",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r.krs,
                    r.rok,
                    r.kod,
                    r.wartosc if r.wartosc is not None else BRAK,
                    r.status,
                    r.dokument,
                    r.lokalizator,
                    r.zrodlo_sha256,
                    r.pobrano,
                    r.kontrola,
                ]
            )
