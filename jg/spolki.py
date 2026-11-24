"""Spółki komunalne (municipal companies): verify `spolki.yaml` candidates against the full
KRS extract (odpis pełny) from the KRS API.

A company is municipal when a current shareholder is a gmina (municipality) or another municipal
company on the list (a subsidiary of a municipal company). A gmina shareholder is identified by
REGON: the shareholder's REGON in KRS is matched against the unit REGON in the Rb-NDS file
(e.g. Oława 931934845). The name is not enough („GMINA MIEJSKA W OŁAWIE”, „GMINA MIASTO OŁAWA”).
The gmina established from the extract is compared with the gmina given on the list; a mismatch
is recorded in the `powod` field.

Output: company and shareholders with document (full KRS extract), position, hash, fetch date.
Governing bodies: only roles and head counts (the API masks names; GDPR: persons only in
connection with a public function). A candidate whose extract could not be fetched or read gets
a `brak danych` row with the reason.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from jg import krs, mf_rb
from jg.budzet import BRAK
from jg.fetch.archive import ArchivedFile
from jg.teryt import gmina_teryt

KRS_URL = "https://api-krs.ms.gov.pl/api/krs/OdpisPelny/{krs}?rejestr=P&format=json"


@dataclass(frozen=True)
class Kandydat:
    krs: str
    gmina: str
    wskazanie: str


def wczytaj_liste(path: Path) -> tuple[str, list[Kandydat]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    out = [
        Kandydat(str(s["krs"]).zfill(10), str(s["gmina"]), s["wskazanie"]) for s in data["spolki"]
    ]
    return data.get("zakres", "probka"), out


@dataclass
class Regony:
    gminy: dict[str, str]  # REGON (9 digits) → gmina code
    rok: int | None
    kwartal: str | None


def regony_gmin(archive: Path) -> Regony:
    """REGONs of gmina units from the Rb-NDS file. One REGON under two codes, or differing
    periods within the file, is an error."""
    out: dict[str, str] = {}
    okres = mf_rb.Okres()
    names = (*mf_rb.KEY_FIELDS, *mf_rb.PERIOD_FIELDS, "REGON")
    with mf_rb.dbf_member(archive, "RbNDS") as (mm, header, _):
        for _, values in mf_rb.scan(mm, header, names):
            teryt = gmina_teryt(*values[:5])
            if teryt is None or not values[7]:
                continue
            okres.check(values[5], values[6])
            regon = values[7][:9]
            if out.get(regon, teryt) != teryt:
                raise mf_rb.MfFormatError(f"Rb-NDS: REGON {regon} dla {out[regon]} i {teryt}")
            out[regon] = teryt
    return Regony(out, okres.rok, okres.kwartal)


@dataclass
class Wynik:
    kandydat: Kandydat
    spolka: krs.Spolka | None
    zrodlo: ArchivedFile | None
    komunalna: bool = False
    powod: str = ""
    gmina_ustalona: str | None = None
    gminy_wspolnicy: dict[int, str] = field(default_factory=dict)  # shareholder index → gmina
    spolki_wspolnicy: dict[int, str] = field(
        default_factory=dict
    )  # index → KRS of a listed company


def weryfikuj(
    odpisy: dict[str, tuple[krs.Spolka, ArchivedFile]],
    kandydaci: list[Kandydat],
    regony: dict[str, str],
    bledy: dict[str, str] | None = None,
) -> list[Wynik]:
    """Verify candidates; subsidiaries of municipal companies are resolved iteratively.
    `bledy`: KRS → reason the extract is missing (fetch or read failure)."""
    wyniki: dict[str, Wynik] = {}
    for k in kandydaci:
        if k.krs not in odpisy:
            powod = (bledy or {}).get(k.krs, "brak odpisu")
            wyniki[k.krs] = Wynik(k, None, None, False, f"{BRAK}: {powod}")
            continue
        s, src = odpisy[k.krs]
        gminy = {
            i: regony[w.regon] for i, w in enumerate(s.wspolnicy) if w.regon and w.regon in regony
        }
        spolki = {i: w.krs for i, w in enumerate(s.wspolnicy) if w.obecny and w.krs in odpisy}
        wyniki[k.krs] = Wynik(k, s, src, gminy_wspolnicy=gminy, spolki_wspolnicy=spolki)
    zmiana = True
    while zmiana:
        zmiana = False
        for w in wyniki.values():
            if w.komunalna or w.spolka is None:
                continue
            obecne = [t for i, t in w.gminy_wspolnicy.items() if w.spolka.wspolnicy[i].obecny]
            matki = [
                wyniki[k]
                for k in w.spolki_wspolnicy.values()
                if k in wyniki and wyniki[k].komunalna
            ]
            g = w.kandydat.gmina
            if obecne:
                w.gmina_ustalona = g if g in obecne else obecne[0]
                w.powod = "wspólnik: gmina (REGON z Rb-NDS)"
            elif matki:
                gminy_matek = [m.gmina_ustalona for m in matki]
                w.gmina_ustalona = g if g in gminy_matek else gminy_matek[0]
                w.powod = "wspólnik: spółka komunalna z listy"
            else:
                continue
            w.komunalna = True
            if w.gmina_ustalona != g:
                w.powod += f"; gmina z odpisu {w.gmina_ustalona} ≠ wskazana {g}"
            zmiana = True
    for w in wyniki.values():
        if w.spolka is not None and not w.komunalna:
            w.powod = "brak wspólnika-gminy ani spółki komunalnej wśród obecnych wspólników"
    return list(wyniki.values())


def _udzial(ws: krs.Wspolnik, kapital) -> str:
    """Percentage share only for current shareholders: the share capital in the extract is the
    current one, and the capital for a former shareholder's period is not computed."""
    if not ws.obecny:
        return f"{BRAK}: były wspólnik (udział wobec kapitału z tamtego okresu nie jest liczony)"
    value = ws.udzial_proc(kapital)
    if value is not None:
        return str(value)
    if kapital is None:
        return f"{BRAK}: brak kapitału zakładowego w odpisie"
    return f"{BRAK}: brak kwoty udziałów w odpisie"


def _lub(value, przyczyna: str) -> str:
    return str(value) if value not in (None, "") else f"{BRAK}: {przyczyna}"


def _organ(funkcje: list[str] | None, liczba: bool) -> str:
    if funkcje is None:
        return f"{BRAK}: brak organu albo działu 2 w odpisie"
    if liczba:
        return str(len(funkcje))
    return "; ".join(funkcje) if funkcje else "0 osób w organie"


def zapisz(dane: Path, wyniki: list[Wynik], zakres: str) -> None:
    dane.mkdir(parents=True, exist_ok=True)
    with (dane / "spolki.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "krs",
                "nazwa",
                "forma",
                "nip",
                "regon",
                "siedziba_gmina",
                "gmina_wskazana",
                "gmina_ustalona",
                "komunalna",
                "powod",
                "kapital_zakladowy",
                "zarzad_funkcje",
                "zarzad_osob",
                "rada_nadzorcza_osob",
                "sprawozdania_zlozone",
                "stan_z_dnia",
                "zakres",
                "wskazanie",
                "dokument",
                "zrodlo_sha256",
                "pobrano",
            ]
        )
        for r in sorted(wyniki, key=lambda r: r.kandydat.krs):
            s, k = r.spolka, r.kandydat
            if s is None or r.zrodlo is None:
                b = r.powod
                w.writerow(
                    [
                        k.krs,
                        b,
                        b,
                        b,
                        b,
                        b,
                        k.gmina,
                        b,
                        0,
                        b,
                        b,
                        b,
                        b,
                        b,
                        b,
                        b,
                        zakres,
                        k.wskazanie,
                        "API KRS, odpis pełny",
                        b,
                        b,
                    ]
                )
                continue
            zarzad = s.organy.get("zarząd")
            w.writerow(
                [
                    s.krs,
                    s.nazwa,
                    _lub(s.forma, "brak formy prawnej w odpisie"),
                    _lub(s.nip, "brak NIP w odpisie"),
                    _lub(s.regon, "brak REGON w odpisie"),
                    _lub(s.siedziba_gmina, "brak siedziby w odpisie"),
                    k.gmina,
                    _lub(r.gmina_ustalona, "spółka niekomunalna"),
                    int(r.komunalna),
                    r.powod,
                    _lub(s.kapital, "brak kapitału zakładowego w odpisie"),
                    _organ(zarzad, False),
                    _organ(zarzad, True),
                    _organ(s.organy.get("rada nadzorcza"), True),
                    len(s.sprawozdania)
                    if s.sprawozdania is not None
                    else f"{BRAK}: brak działu 3 w odpisie",
                    s.stan_z_dnia.isoformat(),
                    zakres,
                    k.wskazanie,
                    "API KRS, odpis pełny",
                    r.zrodlo.sha256,
                    r.zrodlo.fetched_at,
                ]
            )
    with (dane / "spolki_wspolnicy.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "krs",
                "wspolnik_nazwa",
                "wspolnik_teryt",
                "wspolnik_krs",
                "wspolnik_regon",
                "wartosc_udzialow",
                "udzial_proc",
                "od",
                "do",
                "lokalizator",
                "dokument",
                "zrodlo_sha256",
                "pobrano",
            ]
        )
        for r in sorted(wyniki, key=lambda r: r.kandydat.krs):
            s = r.spolka
            if s is None or r.zrodlo is None:
                continue
            for i, ws in enumerate(s.wspolnicy):
                teryt = r.gminy_wspolnicy.get(i) or (
                    f"{BRAK}: wspólnik nie jest gminą"
                    if ws.krs or not ws.regon
                    else f"{BRAK}: REGON wspólnika nie jest REGON-em gminy w Rb-NDS"
                )
                if ws.obecny:
                    do = "obecnie"
                else:
                    do = ws.do.isoformat() if ws.do else f"{BRAK}: brak daty wykreślenia"
                w.writerow(
                    [
                        s.krs,
                        ws.nazwa,
                        teryt,
                        ws.krs or f"{BRAK}: wspólnik bez numeru KRS",
                        ws.regon or f"{BRAK}: brak REGON wspólnika w odpisie",
                        ws.wartosc_udzialow
                        if ws.wartosc_udzialow is not None
                        else f"{BRAK}: brak kwoty udziałów w odpisie",
                        _udzial(ws, s.kapital),
                        ws.od.isoformat() if ws.od else f"{BRAK}: brak daty wpisu",
                        do,
                        ws.lokalizator,
                        "API KRS, odpis pełny",
                        r.zrodlo.sha256,
                        r.zrodlo.fetched_at,
                    ]
                )


def odpis_z_archiwum(item: ArchivedFile) -> krs.Spolka:
    return krs.parse(json.loads(item.read_bytes()))
