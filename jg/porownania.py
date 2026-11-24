"""Comparison groups and medians.

Group = gmina type (urban, urban-rural, rural, city with powiat status) × population band in the
same year (defined in `metrics.yaml`, section `grupy`). The gmina type in a given year comes
from that year's TERYT code: when a year's data come from a code predating a type change (note
„kod w plikach MF <rok>: …” on budget values), the group is the type from that code. Cities
with powiat status form separate groups: their reports cover both gmina and powiat tasks.

For each comparison indicator, year and group: number of gminy with a value, minimum, first
quartile, median, third quartile, maximum (quartiles via `statistics.quantiles(method="inclusive")`,
computed and compared without rounding; rounding only on output). A gmina's position is described
mechanically: quartile band and difference from the median. No rankings.
"""

from __future__ import annotations

import csv
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from jg.budzet import BRAK, Wartosc
from jg.format import number
from jg.metrics import GrupyDef, ProgiLudnosci
from jg.teryt import TYPY

KOD_ROKU = re.compile(r"kod w plikach MF \d{4}: (\d{6})([123])")
Q = Decimal("0.0001")


@dataclass(frozen=True)
class Gmina:
    teryt: str
    typ: str
    mnpp: bool


def typ_w_roku(g: Gmina, budzet_roku: Wartosc | None) -> str:
    """Gmina type in a year: from the code in that year's MF file (note on the value),
    otherwise the current type."""
    if g.mnpp or budzet_roku is None:
        return g.typ
    m = KOD_ROKU.search(budzet_roku.kontrola)
    return TYPY[m.group(2)] if m else g.typ


def przedzial(ludnosc: Decimal, progi: ProgiLudnosci) -> str:
    dol = 0
    for gorna in progi.przedzialy_ludnosci:
        if ludnosc < gorna:
            return f"{number(dol)}–{number(gorna - 1)}"
        dol = gorna
    return f"{number(dol)} i więcej"


def grupa(g: Gmina, ludnosc: Decimal | None, grupy: GrupyDef, typ: str | None = None) -> str | None:
    if ludnosc is None:
        return None
    if g.mnpp:
        return f"miasto na prawach powiatu, ludność {przedzial(ludnosc, grupy.mnpp)}"
    return f"gmina {typ or g.typ}, ludność {przedzial(ludnosc, grupy)}"


@dataclass(frozen=True)
class Statystyki:
    n: int
    minimum: Decimal
    q1: Decimal
    mediana: Decimal
    q3: Decimal
    maksimum: Decimal


def statystyki(values: list[Decimal]) -> Statystyki:
    """Statistics without rounding (`statistics.quantiles` on Decimal returns Decimal)."""
    v = sorted(values)
    q1, med, q3 = statistics.quantiles(v, n=4, method="inclusive")
    return Statystyki(len(v), v[0], Decimal(q1), Decimal(med), Decimal(q3), v[-1])


def polozenie(value: Decimal, s: Statystyki) -> str:
    if value < s.q1:
        return "poniżej pierwszego kwartyla"
    if value < s.mediana:
        return "między pierwszym kwartylem a medianą"
    if value == s.mediana:
        return "równa medianie"
    if value <= s.q3:
        return "między medianą a trzecim kwartylem"
    return "powyżej trzeciego kwartyla"


@dataclass(frozen=True)
class Wiersz:
    teryt: str
    rok: int
    kod: str
    grupa: str
    wartosc: Decimal | None
    status: str
    stat: Statystyki | None
    polozenie: str
    roznica_od_mediany: Decimal | None
    zrodlo: Wartosc | None  # input value (document, hash, fetch date)


def porownaj(
    gminy: list[Gmina],
    wartosci: list[Wartosc],
    kody: list[str],
    lata: list[int],
    grupy: GrupyDef,
) -> list[Wiersz]:
    by = {(w.teryt, w.rok, w.kod): w for w in wartosci}
    out = []
    for rok in lata:
        przyp: dict[str, str | None] = {}
        for g in gminy:
            lud = by.get((g.teryt, rok, "ludnosc"))
            ok = lud is not None and lud.status == "ok" and lud.wartosc is not None
            typ = typ_w_roku(g, by.get((g.teryt, rok, "dochody_wykonanie")))
            przyp[g.teryt] = grupa(g, lud.wartosc if ok else None, grupy, typ)
        for kod in kody:
            czlonkowie: dict[str, list[Decimal]] = defaultdict(list)
            for g in gminy:
                w = by.get((g.teryt, rok, kod))
                gr = przyp[g.teryt]
                if gr and w and w.status == "ok" and w.wartosc is not None:
                    czlonkowie[gr].append(w.wartosc)
            stat = {gr: statystyki(v) for gr, v in czlonkowie.items() if len(v) >= grupy.min_n}
            for g in gminy:
                out.append(_wiersz(g, rok, kod, przyp[g.teryt], by, stat, czlonkowie, grupy))
    return out


def _wiersz(g, rok, kod, gr, by, stat, czlonkowie, grupy) -> Wiersz:
    w = by.get((g.teryt, rok, kod))
    if w is None:
        wartosc, przyczyna = None, f"{BRAK}: brak wartości {kod} za {rok}"
    elif w.status != "ok":
        wartosc, przyczyna = None, w.status
    elif w.wartosc is None:
        wartosc, przyczyna = None, f"{BRAK}: brak wartości {kod} za {rok}"
    else:
        wartosc, przyczyna = w.wartosc, None
    if gr is None:
        status = przyczyna or f"{BRAK}: brak ludności za {rok} (grupa nieznana)"
        return Wiersz(
            g.teryt,
            rok,
            kod,
            f"{BRAK}: brak ludności za {rok}",
            wartosc,
            status,
            None,
            BRAK,
            None,
            w,
        )
    s = stat.get(gr)
    if przyczyna:
        return Wiersz(g.teryt, rok, kod, gr, None, przyczyna, s, BRAK, None, w)
    if s is None:
        n = len(czlonkowie.get(gr, []))
        status = f"{BRAK}: grupa ma {n} gmin z wartością (minimum {grupy.min_n})"
        return Wiersz(g.teryt, rok, kod, gr, wartosc, status, None, BRAK, None, w)
    return Wiersz(
        g.teryt, rok, kod, gr, wartosc, "ok", s, polozenie(wartosc, s), wartosc - s.mediana, w
    )


def _q(v: Decimal) -> Decimal:
    return v.quantize(Q, ROUND_HALF_UP)


def zapisz(path: Path, rows: list[Wiersz]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "teryt",
                "rok",
                "kod",
                "grupa",
                "wartosc",
                "status",
                "n",
                "minimum",
                "q1",
                "mediana",
                "q3",
                "maksimum",
                "polozenie",
                "roznica_od_mediany",
                "dokument",
                "zrodlo_sha256",
                "pobrano",
                "metoda",
            ]
        )
        for r in rows:
            s = r.stat
            stats = (
                [s.n, _q(s.minimum), _q(s.q1), _q(s.mediana), _q(s.q3), _q(s.maksimum)]
                if s
                else [BRAK] * 6
            )
            src = r.zrodlo
            w.writerow(
                [
                    r.teryt,
                    r.rok,
                    r.kod,
                    r.grupa,
                    r.wartosc if r.wartosc is not None else BRAK,
                    r.status,
                    *stats,
                    r.polozenie,
                    _q(r.roznica_od_mediany) if r.roznica_od_mediany is not None else BRAK,
                    src.dokument if src else BRAK,
                    src.zrodlo_sha256 if src else BRAK,
                    src.pobrano if src else BRAK,
                    "statystyki grupy z wartości tego wskaźnika i roku w data/wartosci.csv; "
                    "kwartyle statistics.quantiles(method='inclusive'); grupa: metrics.yaml",
                ]
            )
