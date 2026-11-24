"""E-sprawozdanie finansowe (financial statement XML, MF schema `JednostkaInna`) from RDF
(S6, manual mode).

Based on okiemnaolawe `parsers/esprawozdanie.py` (without database writes). File layout:
`Naglowek` (OkresOd, OkresDo), `WprowadzenieDoSprawozdaniaFinansowego/P_1` (name, NIP, KRS),
`Bilans`, `RZiS` (`RZiSKalk` or `RZiSPor`), `RachPrzeplywow` (`PrzeplywyPosr` or
`PrzeplywyBezp`, optional). A position has `KwotaA` (current year) and `KwotaB` (previous year).
The file may be inside a document envelope (`TrescDokumentu/JednostkaInna`).

Indicators (metrics.yaml): net revenue (RZiS A), net result (RZiSKalk O / RZiSPor L),
capital expenditure = outlays on acquiring intangible assets and property, plant and equipment
(RachPrzeplywow B.II.1). No cash flow statement = capital expenditure `brak danych`.

Checks on column KwotaA: assets = liabilities and equity, sum of asset and liability items =
totals, gross result − tax − other charges = net result. A mismatch blocks publication of all
values from the statement.
"""

from __future__ import annotations

import base64
import binascii
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

TOLERANCE = Decimal("0.01")
RZIS = {
    "RZiSKalk": {"przychody_netto": ["A"], "wynik_netto": ["O"]},
    "RZiSPor": {"przychody_netto": ["A"], "wynik_netto": ["L"]},
}
NAKLADY = ["B", "B_II", "B_II_1"]


class StatementError(Exception):
    pass


class BalanceError(StatementError):
    pass


def local(tag: str) -> str:
    return tag.split("}")[-1]


def child(el: ET.Element | None, name: str) -> ET.Element | None:
    if el is None:
        return None
    return next((c for c in el if local(c.tag) == name), None)


def walk(el: ET.Element | None, path: list[str]) -> ET.Element | None:
    for name in path:
        el = child(el, name)
        if el is None:
            return None
    return el


def text(el: ET.Element | None, name: str) -> str | None:
    c = child(el, name)
    return c.text.strip() if c is not None and c.text else None


def kwota_a(el: ET.Element | None, mnoznik: int = 1) -> Decimal | None:
    value = text(el, "KwotaA")
    if not value:
        return None
    try:
        return Decimal(value) * mnoznik
    except InvalidOperation as exc:
        raise StatementError(f"KwotaA {value!r} nie jest liczbą") from exc


def mnoznik_kwot(statement: ET.Element) -> int:
    """Amount unit from `Naglowek/KodSprawozdania`: …WZlotych = 1, …WTysiacach = 1000."""
    kod = text(child(statement, "Naglowek"), "KodSprawozdania") or ""
    if kod.endswith("WZlotych"):
        return 1
    if kod.endswith("WTysiacach"):
        return 1000
    raise StatementError(f"nieznana jednostka kwot (KodSprawozdania {kod!r})")


def find_statement(root: ET.Element, depth: int = 0) -> ET.Element:
    """The `JednostkaInna` element; in a XAdES signature envelope the content may be base64
    in `ds:Object`."""
    for el in root.iter():
        if local(el.tag) == "JednostkaInna":
            return el
    if depth < 2:
        for el in root.iter():
            if local(el.tag) == "Object" and el.text and el.text.strip():
                try:
                    inner = base64.b64decode(el.text.strip(), validate=False)
                    return find_statement(ET.fromstring(inner), depth + 1)
                except (binascii.Error, ET.ParseError, StatementError):
                    continue
    raise StatementError("brak elementu JednostkaInna (e-sprawozdanie) w pliku")


@dataclass
class Sprawozdanie:
    okres_od: date
    okres_do: date
    nazwa: str
    krs: str | None
    nip: str | None
    wariant_rzis: str | None
    metoda_przeplywow: str | None
    wartosci: dict[str, tuple[Decimal | None, str]] = field(default_factory=dict)
    # indicator → (KwotaA, locator)
    kontrola: str = "ok"
    sprawdzono: str = ""  # description of the checks actually performed
    mnoznik: int = 1

    @property
    def rok(self) -> int:
        return self.okres_do.year


def _identifiers(p1: ET.Element | None) -> tuple[str | None, str | None]:
    """2018 schema: `P_1D/KRS` or `P_1D/NIP`; 2025 schema: `P_1D` = NIP, `P_1E` = KRS."""
    p1d = child(p1, "P_1D")
    if p1d is not None and len(p1d):
        return text(p1d, "KRS"), text(p1d, "NIP")
    return text(p1, "P_1E"), text(p1, "P_1D")


def _sum_children(el: ET.Element, prefix: str) -> Decimal:
    return sum(
        (kwota_a(c) or Decimal(0) for c in el if local(c.tag).startswith(prefix)), Decimal(0)
    )


def _close(a: Decimal | None, b: Decimal | None) -> bool:
    return a is not None and b is not None and abs(a - b) <= TOLERANCE


def check_balance(
    bilans: ET.Element | None, wariant: str | None, rzis: ET.Element | None
) -> list[str]:
    """Checks performed (description); a mismatch raises BalanceError."""
    if bilans is None:
        raise BalanceError("brak sekcji Bilans")
    aktywa, pasywa = child(bilans, "Aktywa"), child(bilans, "Pasywa")
    a, p = kwota_a(aktywa), kwota_a(pasywa)
    if not _close(a, p):
        raise BalanceError(f"bilans: aktywa {a} ≠ pasywa {p}")
    if not _close(a, _sum_children(aktywa, "Aktywa_")):
        raise BalanceError(f"bilans: suma pozycji aktywów ≠ aktywa razem {a}")
    if not _close(p, _sum_children(pasywa, "Pasywa_")):
        raise BalanceError(f"bilans: suma pozycji pasywów ≠ pasywa razem {p}")
    done = ["aktywa = pasywa", "suma pozycji bilansu = razem"]
    keys = {"RZiSKalk": ("L", "M", "N", "O"), "RZiSPor": ("I", "J", "K", "L")}.get(wariant or "")
    if keys is None:
        return [*done, "RZiS niesprawdzony (nieznany wariant)"]
    gross, tax, other, net = (kwota_a(child(rzis, k)) for k in keys)
    if gross is None or net is None:
        return [*done, "RZiS niesprawdzony (brak wyniku brutto lub netto)"]
    expected = gross - (tax or Decimal(0)) - (other or Decimal(0))
    if not _close(expected, net):
        raise BalanceError(f"RZiS: wynik brutto − podatek ≠ wynik netto ({expected} ≠ {net})")
    return [*done, "wynik brutto − podatek − pozostałe = wynik netto"]


def parse(content: bytes) -> Sprawozdanie:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise StatementError(f"plik nie jest poprawnym XML: {exc}") from exc
    st = find_statement(root)
    nag = child(st, "Naglowek")
    od, do = text(nag, "OkresOd"), text(nag, "OkresDo")
    if not od or not do:
        raise StatementError("nagłówek bez OkresOd/OkresDo")
    p1 = child(child(st, "WprowadzenieDoSprawozdaniaFinansowego"), "P_1")
    krs, nip = _identifiers(p1)
    bilans = child(st, "Bilans")
    rzis_root = child(st, "RZiS")
    rzis = next(iter(rzis_root), None) if rzis_root is not None else None
    wariant = local(rzis.tag) if rzis is not None else None
    cash_root = child(st, "RachPrzeplywow")
    cash = next(iter(cash_root), None) if cash_root is not None else None
    metoda = local(cash.tag) if cash is not None else None
    s = Sprawozdanie(
        date.fromisoformat(od),
        date.fromisoformat(do),
        text(child(p1, "P_1A"), "NazwaFirmy") or "brak danych",
        krs,
        nip,
        wariant,
        metoda,
    )
    s.mnoznik = mnoznik_kwot(st)
    jednostka = "" if s.mnoznik == 1 else " × 1000 (sprawozdanie w tysiącach zł)"
    try:
        s.sprawdzono = "; ".join(check_balance(bilans, wariant, rzis))
    except BalanceError as exc:
        s.kontrola = str(exc)
        s.sprawdzono = f"niezgodność: {exc}"
    for kod, path in RZIS.get(wariant or "", {}).items():
        s.wartosci[kod] = (
            kwota_a(walk(rzis, path), s.mnoznik),
            f"RZiS/{wariant}/" + "/".join(path) + jednostka,
        )
    if cash is not None:
        s.wartosci["naklady_inwestycyjne"] = (
            kwota_a(walk(cash, NAKLADY), s.mnoznik),
            f"RachPrzeplywow/{metoda}/" + "/".join(NAKLADY) + jednostka,
        )
    return s
