"""Liabilities (Rb-Z), population (BDL) and derived indicators from `metrics.yaml`.

All values use the same record as `jg.budzet.Wartosc`: document, position, hash, fetch date,
status (`ok` or `brak danych: <reason>`). `derived` indicators store the formula and input
values in the `kontrola` field.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from jg import bdl, calc
from jg.budzet import BRAK, Wartosc
from jg.fetch.archive import ArchivedFile
from jg.metrics import MetricsFile
from jg.rb_z import PlikRbZ
from jg.teryt import kod_w_roku

DOKLADNOSC = Decimal("0.0001")


def wartosci_rbz(rok: int, gminy: list[str], plik: PlikRbZ, src: ArchivedFile) -> list[Wartosc]:
    out = []
    dokument = f"Rb-Z {rok}, po IV kwartałach (baza MF), część RbZtd"
    slownik = set(gminy)
    for teryt in gminy:
        kod_pliku, uwaga = kod_w_roku(teryt, plik.gminy, slownik, f"w Rb-Z {rok}")
        z = plik.gminy.get(kod_pliku) if kod_pliku else None
        if z is None:
            wartosc, status, kontrola = None, f"{BRAK}: brak gminy w Rb-Z {rok}", BRAK
        elif niezgodne := z.niezgodne():
            wartosc, status = None, f"{BRAK}: Rb-Z ({'; '.join(niezgodne)})"
            kontrola = "; ".join(niezgodne) + (f"; {uwaga}" if uwaga else "")
        else:
            wartosc, status = z.ogolem, "ok"
            kontrola = "E = E1+E2+E3+E4; E = EKO+EZO" + (f"; {uwaga}" if uwaga else "")
        out.append(
            Wartosc(
                teryt,
                rok,
                "zobowiazania_ogolem",
                wartosc,
                status,
                dokument,
                "wiersz E, kwota zadłużenia ogółem (pole E)",
                src.sha256,
                src.fetched_at,
                kontrola,
            )
        )
    return out


def wartosci_ludnosc(
    lata: list[int], gminy: list[str], dane: list[bdl.Wartosc], strony: list[ArchivedFile]
) -> list[Wartosc]:
    """Population from BDL. Hash and fetch date are those of the response page the value came from.

    A gmina that changed type (e.g. rural → urban-rural) has earlier years in BDL under that
    year's code: the same WWPPGG, a different type digit. Such a code (the only candidate outside
    the dictionary) is used, with a note in the `kontrola` field."""
    by: dict[tuple[str, int], tuple[bdl.Wartosc, ArchivedFile]] = {}
    kody_roku: dict[int, set[str]] = {}
    for w, src in dane_ze_zrodlem(dane, strony):
        by[(w.teryt, w.rok)] = (w, src)
        kody_roku.setdefault(w.rok, set()).add(w.teryt)
    znane = set(gminy)
    out = []
    for teryt in gminy:
        for rok in lata:
            kod, uwaga = kod_w_roku(teryt, kody_roku.get(rok, set()), znane, f"BDL w {rok}")
            found = by.get((kod, rok)) if kod else None
            uwaga = f"; {uwaga}" if uwaga else ""
            if found is None:
                out.append(
                    Wartosc(
                        teryt,
                        rok,
                        "ludnosc",
                        None,
                        f"{BRAK}: brak gminy w BDL za {rok}",
                        "GUS BDL, zmienna 72305",
                        BRAK,
                        BRAK,
                        BRAK,
                        BRAK,
                    )
                )
                continue
            w, src = found
            status = "ok" if w.wartosc is not None else f"{BRAK}: {w.przyczyna_braku}"
            out.append(
                Wartosc(
                    teryt,
                    rok,
                    "ludnosc",
                    Decimal(w.wartosc) if w.wartosc is not None else None,
                    status,
                    "GUS BDL, zmienna 72305 (ludność ogółem, 31 XII)",
                    f"by-variable/72305, jednostka BDL {w.bdl_id} ({w.nazwa_bdl}), rok {rok}",
                    src.sha256,
                    src.fetched_at,
                    f"atrybut BDL {w.atrybut}{uwaga}",
                )
            )
    return out


def dane_ze_zrodlem(
    dane: list[bdl.Wartosc], strony: list[ArchivedFile]
) -> list[tuple[bdl.Wartosc, ArchivedFile]]:
    """(value, page) pair; `dane` must have a `strona` field (index into `strony`)."""
    return [(w, strony[w.strona]) for w in dane]


def pochodne(
    metrics: MetricsFile, rok: int, gminy: list[str], fakty: list[Wartosc]
) -> list[Wartosc]:
    """`derived` indicators for a year. Each value has the formula, input values, and the
    documents, hashes and fetch dates of the inputs. A `brak danych` input carries its reason."""
    by = {(f.teryt, f.rok, f.kod): f for f in fakty}
    out = []
    for m in metrics.metrics:
        if m.kind != "derived" or "gmina" not in m.entity_kinds:
            continue
        for teryt in gminy:
            wejscie, przyczyny, zrodla = {}, {}, []
            for kod in m.inputs:
                f = by.get((teryt, rok, kod))
                if f is None:
                    wejscie[kod], przyczyny[kod] = None, f"brak wartości {kod} za {rok}"
                    continue
                wejscie[kod] = f.wartosc if f.status == "ok" else None
                if f.status != "ok":
                    przyczyny[kod] = f.status
                zrodla.append(f)
            w = calc.oblicz(m, wejscie, przyczyny)
            wartosc = (
                w.wartosc.quantize(DOKLADNOSC, ROUND_HALF_UP) if w.wartosc is not None else None
            )
            opis = "; ".join(
                f"{f.kod} = {f.wartosc if f.wartosc is not None else BRAK} ({f.dokument})"
                for f in zrodla
            )
            hashe = ";".join(sorted({f.zrodlo_sha256 for f in zrodla})) or BRAK
            daty = sorted(f.pobrano for f in zrodla if f.pobrano != BRAK)
            out.append(
                Wartosc(
                    teryt,
                    rok,
                    m.id,
                    wartosc,
                    w.status,
                    "wyliczenie (metrics.yaml)",
                    f"wzór: {w.wzor}",
                    hashe,
                    daty[-1] if daty else BRAK,
                    opis or BRAK,
                )
            )
    return out
