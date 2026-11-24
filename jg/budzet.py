"""Gmina budgets from Rb-27S / Rb-28S / Rb-NDS: indicator values with source and checks."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from jg import mf_rb
from jg.fetch.archive import ArchivedFile
from jg.teryt import kod_w_roku

BRAK = "brak danych"
NAZWY_SPRAWOZDAN = {"Rb27S": "Rb-27S", "Rb28S": "Rb-28S", "RbNDS": "Rb-NDS"}


@dataclass(frozen=True)
class Wartosc:
    teryt: str
    rok: int
    kod: str
    wartosc: Decimal | None
    status: str  # "ok" or the reason data is missing
    dokument: str
    lokalizator: str
    zrodlo_sha256: str
    pobrano: str
    kontrola: str


def wartosci(
    rok: int,
    gminy: list[str],
    pliki: dict[str, ArchivedFile],
    rb27: mf_rb.Plik,
    rb28: mf_rb.Plik,
    nds: dict[str, dict[str, Decimal]],
) -> list[Wartosc]:
    """Budget indicator values for the given gminy. A gmina missing from a report, or a
    mismatch with Rb-NDS, yields `brak danych` with the reason (every value has a source; missing
    values carry a reason)."""
    kontrole = {(k.teryt, k.nazwa): k for k in mf_rb.bilansuj(rb27.gminy, rb28.gminy, nds)}
    slownik = set(gminy)
    w_plikach = set(rb27.gminy) | set(rb28.gminy) | set(nds)
    out = []
    for teryt in gminy:
        kod_pliku, uwaga = kod_w_roku(teryt, w_plikach, slownik, f"w plikach MF {rok}")
        kod_pliku = kod_pliku or teryt
        for kdef in mf_rb.KONTROLE:
            kod, rep, lok, pole = kdef.kod, kdef.report, kdef.lokalizator, kdef.pole_nds
            plik = pliki[rep]
            dokument = f"{NAZWY_SPRAWOZDAN[rep]} {rok}, po IV kwartałach (baza MF)"
            sumy = (rb27 if rep == "Rb27S" else rb28).gminy.get(kod_pliku)
            k = kontrole.get((kod_pliku, kod))
            if sumy is None:
                wartosc, status = None, f"{BRAK}: brak gminy w {NAZWY_SPRAWOZDAN[rep]} {rok}"
            elif k is None or k.nds is None:
                wartosc, status = None, f"{BRAK}: brak gminy w Rb-NDS {rok} (kontrola)"
            elif not k.zgodna:
                wartosc, status = None, f"{BRAK}: niezgodność z Rb-NDS {pole} ({k.rb} ≠ {k.nds})"
            else:
                wartosc, status = k.rb, "ok"
            kontrola = f"Rb-NDS {pole} = {k.nds}" if k and k.nds is not None else BRAK
            if uwaga:
                kontrola += f"; {uwaga}"
            out.append(
                Wartosc(
                    teryt,
                    rok,
                    kod,
                    wartosc,
                    status,
                    dokument,
                    lok,
                    plik.sha256,
                    plik.fetched_at,
                    kontrola,
                )
            )
    return out


def zapisz(path: Path, rows: list[Wartosc]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "teryt",
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
                    r.teryt,
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


def wczytaj(path: Path) -> list[Wartosc]:
    """Read a file written by `zapisz`."""
    out = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            wartosc = None if r["wartosc"] == BRAK else Decimal(r["wartosc"])
            out.append(
                Wartosc(
                    r["teryt"],
                    int(r["rok"]),
                    r["kod"],
                    wartosc,
                    r["status"],
                    r["dokument"],
                    r["lokalizator"],
                    r["zrodlo_sha256"],
                    r["pobrano"],
                    r["kontrola"],
                )
            )
    return out


def zapisz_dzialy(
    path: Path,
    rok: int,
    gminy: list[str],
    pliki: dict[str, ArchivedFile],
    rb27: mf_rb.Plik,
    rb28: mf_rb.Plik,
    status_ogolem: dict[tuple[str, str], str],
) -> None:
    """Plan and execution by dział (revenue and expenditure) with document, position and hash.

    Działy have no separate check in Rb-NDS; their sum is the gmina total, so each row gets the
    check status of the total (`status_ogolem[(teryt, report)]`). A gmina without a report has
    one `brak danych` row with the reason."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "teryt",
                "rok",
                "sprawozdanie",
                "dzial",
                "plan",
                "wykonanie",
                "status",
                "dokument",
                "lokalizator",
                "zrodlo_sha256",
                "pobrano",
            ]
        )
        for plik in (rb27, rb28):
            rep = plik.report
            nazwa = NAZWY_SPRAWOZDAN[rep]
            dokument = f"{nazwa} {rok}, po IV kwartałach (baza MF)"
            src = pliki[rep]
            slownik = set(gminy)
            for teryt in gminy:
                kod_pliku, uwaga = kod_w_roku(teryt, plik.gminy, slownik, f"w {nazwa} {rok}")
                sumy = plik.gminy.get(kod_pliku) if kod_pliku else None
                if sumy is None:
                    w.writerow(
                        [
                            teryt,
                            rok,
                            nazwa,
                            BRAK,
                            BRAK,
                            BRAK,
                            f"{BRAK}: brak gminy w {nazwa} {rok}",
                            dokument,
                            BRAK,
                            src.sha256,
                            src.fetched_at,
                        ]
                    )
                    continue
                status = status_ogolem.get((teryt, rep), f"{BRAK}: brak kontroli")
                for dzial, (plan, wyk) in sorted(sumy.dzialy.items()):
                    ok = status == "ok"
                    w.writerow(
                        [
                            teryt,
                            rok,
                            nazwa,
                            dzial,
                            plan if ok else BRAK,
                            wyk if ok else BRAK,
                            status,
                            dokument,
                            f"{nazwa}, dział {dzial}: suma R1 (plan) i R4 (wykonanie)"
                            + (f"; {uwaga}" if uwaga else ""),
                            src.sha256,
                            src.fetched_at,
                        ]
                    )


def status_ogolem(rows: list[Wartosc]) -> dict[tuple[str, str], str]:
    """Check status of the total (plan and execution) for a (gmina, report) pair."""
    kody = {
        "Rb27S": ("dochody_plan", "dochody_wykonanie"),
        "Rb28S": ("wydatki_plan", "wydatki_wykonanie"),
    }
    by = {(r.teryt, r.kod): r.status for r in rows}
    out = {}
    for teryt in {r.teryt for r in rows}:
        for rep, (a, b) in kody.items():
            sa, sb = by.get((teryt, a)), by.get((teryt, b))
            out[(teryt, rep)] = "ok" if sa == sb == "ok" else (sa if sa != "ok" else sb)
    return out
