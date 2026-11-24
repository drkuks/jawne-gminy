"""Full extract (odpis pełny) from the KRS API (S5): company, capital, shareholders with
shares, governing bodies.

Based on okiemnaolawe `parsers/krs_odpis.py`. Structure: `odpis.naglowekP` (KRS number, "as of"
date, `wpis` list with dates), `odpis.dane.dzial1` (entity, capital, `wspolnicySpzoo`),
`dzial2` (management board, supervisory board: names are masked by the API, only roles and head
counts are stored), `dzial3` (notes on filed financial statements).
In the full extract every value has `nrWpisuWprow` and optionally `nrWpisuWykr`; the effective
date is the date of the entry with that number.

Shareholder's share: value of shares from the `posiadaneUdzialy` text („… UDZIAŁÓW O ŁĄCZNEJ
WARTOŚCI|WYSOKOŚCI 82 464 215,05 ZŁ”) divided by share capital; `czyPosiadaCaloscUdzialow`
= 100%. Text without an amount = share `brak danych`. Joint-stock companies have no shareholder
list in KRS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

PERIOD = re.compile(r"(\d{2}\.\d{2}\.\d{4})\D+(\d{2}\.\d{2}\.\d{4})")
# „O ŁĄCZNEJ WYSOKOŚCI 75.499.000,00 ZŁ”, „O ŁĄCZNEJ WARTOŚCI UDZIAŁÓW 34.558.000 (…) ZŁOTYCH”,
# „O ŁĄCZNEJ WARTOŚCI 82 464 215,05 ZŁ”: grosze and „ZŁ” are sometimes omitted.
SHARE_VALUE = re.compile(
    r"ŁĄCZNEJ\s+(?:WARTOŚCI|WYSOKOŚCI)(?:\s+UDZIAŁÓW)?\s+(\d{1,3}(?:[ .\u00a0]\d{3})*(?:,\d{2})?)",
    re.I,
)


class OdpisError(Exception):
    pass


def _date(text: str) -> date:
    return datetime.strptime(text, "%d.%m.%Y").date()


def kwota(text: str) -> Decimal:
    """„75.499.000,00” / „82 464 215,05” → Decimal."""
    return Decimal(text.replace(" ", "").replace(" ", "").replace(".", "").replace(",", "."))


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _entries(value, key: str) -> list[dict]:
    return [item if isinstance(item, dict) else {key: item} for item in _as_list(value)]


def _value(item: dict | None, key: str):
    if item is None:
        return None
    return item.get(key, item)


def _current(items: list[dict]) -> dict | None:
    live = [i for i in items if not i.get("nrWpisuWykr")]
    return live[-1] if live else (items[-1] if items else None)


def regon9(value: str | None) -> str | None:
    if value and len(value) == 14 and value.endswith("00000"):
        return value[:9]
    return value or None


@dataclass
class Wspolnik:
    nazwa: str
    regon: str | None
    krs: str | None
    udzialy_tekst: str
    wartosc_udzialow: Decimal | None
    calosc: bool
    od: date | None
    do: date | None  # deletion date (when known)
    lokalizator: str
    obecny: bool = True

    def udzial_proc(self, kapital: Decimal | None) -> Decimal | None:
        if not self.obecny:
            return None
        if self.calosc:
            return Decimal(100)
        if self.wartosc_udzialow is None or not kapital:
            return None
        return (self.wartosc_udzialow / kapital * 100).quantize(Decimal("0.01"))


@dataclass
class Spolka:
    krs: str
    nazwa: str
    forma: str
    nip: str | None
    regon: str | None
    stan_z_dnia: date
    siedziba_gmina: str
    kapital: Decimal | None
    wspolnicy: list[Wspolnik] = field(default_factory=list)
    # body → roles (no persons); None = no dzial2 or no such body in the extract
    organy: dict[str, list[str] | None] = field(default_factory=dict)
    # None = no dzial3 in the extract
    sprawozdania: list[tuple[date, date, date]] | None = field(default_factory=list)


def parse(data: dict) -> Spolka:
    """Company from a KRS API JSON extract (full or current)."""
    odpis = data.get("odpis")
    if not odpis or "dane" not in odpis:
        raise OdpisError("dokument nie jest odpisem z API KRS")
    head = odpis.get("naglowekP") or odpis.get("naglowekA")
    if head is None:
        raise OdpisError("odpis bez nagłówka")
    dates = {int(w["numerWpisu"]): _date(w["dataWpisu"]) for w in head.get("wpis", [])}
    dz1 = odpis["dane"]["dzial1"]

    def when(item: dict, key: str = "nrWpisuWprow") -> date | None:
        n = item.get(key)
        return dates.get(int(n)) if n else None

    podmiot = dz1["danePodmiotu"]
    nazwa = _value(_current(_entries(podmiot["nazwa"], "nazwa")), "nazwa")
    forma = _value(_current(_entries(podmiot.get("formaPrawna"), "formaPrawna")), "formaPrawna")
    ids = _value(
        _current(_entries(podmiot.get("identyfikatory"), "identyfikatory")), "identyfikatory"
    )
    ids = ids if isinstance(ids, dict) else {}
    siedziba_items = _entries(dz1.get("siedzibaIAdres", {}).get("siedziba"), "siedziba")
    siedziba = _value(_current(siedziba_items), "siedziba") or {}
    kap_items = _as_list(dz1.get("kapital", {}).get("wysokoscKapitaluZakladowego"))
    kap = _current([k for k in kap_items if isinstance(k, dict)])
    spolka = Spolka(
        krs=head["numerKRS"],
        nazwa=nazwa,
        forma=forma or "",
        nip=ids.get("nip"),
        regon=regon9(ids.get("regon")),
        stan_z_dnia=_date(head["stanZDnia"]),
        siedziba_gmina=(siedziba.get("gmina") or "") if isinstance(siedziba, dict) else "",
        kapital=kwota(kap["wartosc"]) if kap and kap.get("wartosc") else None,
    )
    for i, holder in enumerate(_as_list(dz1.get("wspolnicySpzoo"))):
        names = _entries(holder.get("nazwa"), "nazwa")
        ident = _value(
            _current(_entries(holder.get("identyfikator"), "identyfikator")), "identyfikator"
        )
        krs_item = _value(_current(_entries(holder.get("krs"), "krs")), "krs")
        holder_krs = krs_item.get("krs") if isinstance(krs_item, dict) else krs_item
        shares = _entries(holder.get("posiadaneUdzialy"), "posiadaneUdzialy")
        share = _current(shares)
        text = str(_value(share, "posiadaneUdzialy") or "")
        m = SHARE_VALUE.search(text)
        calosc_items = _entries(holder.get("czyPosiadaCaloscUdzialow"), "czyPosiadaCaloscUdzialow")
        calosc = bool(_value(_current(calosc_items), "czyPosiadaCaloscUdzialow") is True)
        starts = [when(s) for s in shares if when(s)]
        # A shareholder is former when their shares, or their name and identifier, were struck out.
        ident_items = _entries(holder.get("identyfikator"), "identyfikator")
        groups = [g for g in (shares, names, ident_items) if g]
        struck = [g for g in groups if all(i.get("nrWpisuWykr") for i in g)]
        live = [] if struck else [True]
        ends = [when(i, "nrWpisuWykr") for g in struck for i in g if when(i, "nrWpisuWykr")]
        spolka.wspolnicy.append(
            Wspolnik(
                nazwa=str(_value(_current(names), "nazwa") or ""),
                regon=regon9(ident.get("regon")) if isinstance(ident, dict) else None,
                krs=holder_krs if holder_krs and set(holder_krs) != {"0"} else None,
                udzialy_tekst=text,
                wartosc_udzialow=kwota(m.group(1)) if m else None,
                calosc=calosc and bool(live),
                od=min(starts) if starts else when(names[0]) if names else None,
                do=max(ends) if ends and not live else None,
                obecny=bool(live),
                lokalizator=f"dzial1.wspolnicySpzoo[{i}].posiadaneUdzialy",
            )
        )
    dz2 = odpis["dane"].get("dzial2")
    if dz2 is None:
        spolka.organy = {"zarząd": None, "rada nadzorcza": None}
    else:
        zarzad = [o for o in _as_list(dz2.get("reprezentacja")) if _organ_aktywny(o)]
        rada = [o for o in _as_list(dz2.get("organNadzoru")) if _organ_aktywny(o)]
        spolka.organy["zarząd"] = (
            [_funkcja(m) for o in zarzad for m in _as_list(o.get("sklad")) if _aktywny(m)]
            if zarzad
            else None
        )
        spolka.organy["rada nadzorcza"] = (
            [
                "członek rady nadzorczej"
                for o in rada
                for m in _as_list(o.get("sklad"))
                if _aktywny(m)
            ]
            if rada
            else None
        )
    dz3 = odpis["dane"].get("dzial3")
    if dz3 is None:
        spolka.sprawozdania = None
        return spolka
    wz = dz3.get("wzmiankiOZlozonychDokumentach", {})
    for group in _as_list(wz.get("wzmiankaOZlozeniuRocznegoSprawozdaniaFinansowego")):
        for pos in _as_list(group.get("pozycja", group)):
            m = PERIOD.search(pos.get("zaOkresOdDo", ""))
            if m and pos.get("dataZlozenia"):
                spolka.sprawozdania.append(
                    (_date(m.group(1)), _date(m.group(2)), _date(pos["dataZlozenia"]))
                )
    return spolka


def _aktywny(member: dict) -> bool:
    """Person currently in the body: in the full extract their fields (surname, role) have an
    entry without `nrWpisuWykr`; in the current extract fields carry no entry numbers."""
    for key in ("nazwisko", "funkcjaWOrganie", "identyfikator"):
        items = _entries(member.get(key), key)
        if items:
            return any(not i.get("nrWpisuWykr") for i in items)
    return not member.get("nrWpisuWykr")


def _organ_aktywny(organ: dict) -> bool:
    """Body not struck out: its name (`nazwa` or `nazwaOrganu`) has an entry without
    `nrWpisuWykr`."""
    for key in ("nazwaOrganu", "nazwa"):
        items = _entries(organ.get(key), key)
        if items:
            return any(not i.get("nrWpisuWykr") for i in items)
    return True


def _funkcja(member: dict) -> str:
    item = _current(_entries(member.get("funkcjaWOrganie"), "funkcjaWOrganie"))
    value = _value(item, "funkcjaWOrganie")
    return str(value) if isinstance(value, str) and value else "członek zarządu"


def obecni(spolka: Spolka) -> list[Wspolnik]:
    return [w for w in spolka.wspolnicy if w.obecny]
