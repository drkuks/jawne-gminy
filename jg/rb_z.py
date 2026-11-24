"""Rb-Z (S1): liabilities by debt instrument, part RbZtd (file `RbZ.dbf`).

Fields (MF „Struktura sprawozdań” v10.5, part RbZtd): row E = total liabilities
(E1 securities + E2 credits and loans + E3 deposits received + E4 liabilities due);
column Z (field without suffix, e.g. `E`) = total debt, KO (`EKO`) = to domestic creditors,
ZO (`EZO`) = to foreign creditors. Balance at period end: for an annual value, the file
„po IV kwartałach” (after Q4).

Checks: E = E1 + E2 + E3 + E4 and E = EKO + EZO (tolerance 0.01 zł).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from jg import mf_rb
from jg.teryt import gmina_teryt

POLA = ("E", "E1", "E2", "E3", "E4", "EKO", "EZO")


@dataclass(frozen=True)
class Zobowiazania:
    teryt: str
    pola: dict[str, Decimal]
    puste: tuple[str, ...] = ()  # fields with no value in the record (not zero)

    @property
    def ogolem(self) -> Decimal:
        return self.pola["E"]

    def kontrole(self) -> list[tuple[str, Decimal, Decimal]]:
        p = self.pola
        return [
            ("E = E1+E2+E3+E4", p["E"], p["E1"] + p["E2"] + p["E3"] + p["E4"]),
            ("E = EKO+EZO", p["E"], p["EKO"] + p["EZO"]),
        ]

    def niezgodne(self) -> list[str]:
        if self.puste:
            return [f"puste pola {', '.join(self.puste)}"]
        return [
            f"{opis}: {a} ≠ {b}" for opis, a, b in self.kontrole() if abs(a - b) > mf_rb.TOLERANCE
        ]


@dataclass
class PlikRbZ:
    gminy: dict[str, Zobowiazania]
    member: str
    rok: int | None
    kwartal: str | None


def rb_z(archive: Path) -> PlikRbZ:
    """Gmina liabilities from the Rb-Z archive. Two records for one gmina, or differing periods,
    is an error."""
    out: dict[str, Zobowiazania] = {}
    okres, klucze = mf_rb.Okres(), mf_rb.Klucze("RbZ")
    names = (*mf_rb.KEY_FIELDS, *mf_rb.PERIOD_FIELDS, *POLA)
    with mf_rb.dbf_member(archive, "RbZ") as (mm, header, member):
        for i, values in mf_rb.scan(mm, header, names):
            kod = gmina_teryt(*values[:5])
            if kod is None:
                continue
            klucze.check(kod, tuple(values[:5]))
            okres.check(values[5], values[6])
            if kod in out:
                raise mf_rb.MfFormatError(f"Rb-Z: dwa rekordy gminy {kod}")
            raw = dict(zip(POLA, values[7:], strict=True))
            puste = tuple(k for k, v in raw.items() if v == "")
            pola = {k: mf_rb.num(v, f"RbZ rekord {i} {k}") for k, v in raw.items()}
            out[kod] = Zobowiazania(kod, pola, puste)
    return PlikRbZ(out, member, okres.rok, okres.kwartal)
