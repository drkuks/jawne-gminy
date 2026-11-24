"""JST (local government) budget reports from MF databases (S1): Rb-27S, Rb-28S, Rb-NDS.

Field meanings per the MF "Struktura sprawozdań", version 10.5 (S1 archive "Struktura sprawozdań"):
- Rb-27S (revenue): DZIAL, ROZDZIAL, PAR (3 characters) + PAR4 (4th digit), R1 = plan,
  R4 = revenue executed.
- Rb-28S (expenditure): DZIAL, ROZDZIAL, GRUPA, PAR + PAR4, R1 = plan, R4 = expenditure executed.
  Capital expenditure: expenditure groups 16xx (GRUPA, since 2019: 1600 investment, 1601/1602 EU
  and national share, 1610, 1620, 1630). Some gminy report the plan only in the group row,
  without a paragraph, so the "paragraph 6xxx" criterion gives sums different from Rb-NDS B2
  (checked on 2025 data).
- Rb-NDS: rows A (total revenue), A1 (current), A2 (capital), B (total expenditure),
  B1 (current), B2 (capital); columns P = plan after amendments, W = execution (e.g. `AW`, `B2P`).

Data are cumulative from the start of the year: the annual value is the "po IV kwartałach"
(full-year) file. The file period (ROK, KWARTAL) must be the same in all records. Units: rule
`jg.teryt.gmina_teryt`; powiaty, voivodeships and JST unions are skipped.

The Rb-28S file is about 660 MB with 1.5 million rows, so fields are read directly from `mmap`
at fixed offsets, without decoding the other columns.
"""

from __future__ import annotations

import mmap
import shutil
import tempfile
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

import py7zr

from jg import dbf
from jg.teryt import gmina_teryt

KEY_FIELDS = ("WK", "PK", "GK", "GT", "PT")
PERIOD_FIELDS = ("ROK", "KWARTAL")
REPORTS = {
    "Rb27S": {"DZIAL", "ROZDZIAL", "PAR", "PAR4", "R1", "R2", "R4"},
    "Rb28S": {"DZIAL", "ROZDZIAL", "GRUPA", "PAR", "PAR4", "R1", "R4", "R10"},
    "RbNDS": {"AP", "AW", "BP", "BW", "A1W", "A2W", "B1W", "B2P", "B2W"},
    "RbZ": {"E", "E1", "E2", "E3", "E4", "EKO", "EZO"},
}
NDS_FIELDS = ("AP", "AW", "A1P", "A1W", "A2P", "A2W", "BP", "BW", "B1P", "B1W", "B2P", "B2W")
ZERO = Decimal("0")
GRUPA_MAJATKOWE = "16"
TOLERANCE = Decimal("0.01")


class MfFormatError(Exception):
    pass


@dataclass(frozen=True)
class KontrolaDef:
    """One budget metric: report, sums attribute, Rb-NDS field, item description."""

    kod: str
    report: str
    attr: str
    pole_nds: str
    lokalizator: str


# The single table of cross-checked budget metrics (used by bilansuj and jg.budzet;
# metric descriptions and sources: metrics.yaml).
KONTROLE = (
    KontrolaDef("dochody_plan", "Rb27S", "plan", "AP", "suma R1 wszystkich wierszy"),
    KontrolaDef("dochody_wykonanie", "Rb27S", "wykonanie", "AW", "suma R4 wszystkich wierszy"),
    KontrolaDef("wydatki_plan", "Rb28S", "plan", "BP", "suma R1 wszystkich wierszy"),
    KontrolaDef("wydatki_wykonanie", "Rb28S", "wykonanie", "BW", "suma R4 wszystkich wierszy"),
    KontrolaDef(
        "wydatki_majatkowe_plan", "Rb28S", "plan_majatkowe", "B2P", "suma R1 wierszy GRUPA 16xx"
    ),
    KontrolaDef(
        "wydatki_majatkowe_wykonanie",
        "Rb28S",
        "wykonanie_majatkowe",
        "B2W",
        "suma R4 wierszy GRUPA 16xx",
    ),
)


def _naglowek_pasuje(head: bytes, required: set[str]) -> bool:
    try:
        header = dbf.read_header(head)
    except dbf.DbfError:
        return False
    return required <= set(header.names)


@contextmanager
def dbf_member(archive: Path, report: str) -> Iterator[tuple[mmap.mmap, dbf.Header, str]]:
    """DBF file of the report's main part from a ZIP or 7z archive (identified by its field set;
    MF published some 2020 files as 7z). From a ZIP only the matching file is extracted;
    the name is returned with its full path inside the archive."""
    required = REPORTS[report] | set(KEY_FIELDS) | set(PERIOD_FIELDS)
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "plik.dbf"
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zf:
                found = []
                for info in zf.infolist():
                    if not info.filename.lower().endswith(".dbf"):
                        continue
                    with zf.open(info) as fh:
                        if _naglowek_pasuje(fh.read(64 * 1024), required):
                            found.append(info)
                if len(found) != 1:
                    names = [i.filename for i in found]
                    raise MfFormatError(f"{archive.name}: {report} – pasujące pliki DBF: {names}")
                member = found[0].filename
                with zf.open(found[0]) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
        elif py7zr.is_7zfile(archive):
            with py7zr.SevenZipFile(archive) as zf:
                names = [n for n in zf.getnames() if n.lower().endswith(".dbf")]
                zf.extract(path=tmp, targets=names)
            found = [
                n
                for n in names
                if _naglowek_pasuje((Path(tmp) / n).read_bytes()[: 64 * 1024], required)
            ]
            if len(found) != 1:
                raise MfFormatError(f"{archive.name}: {report} – pasujące pliki DBF: {found}")
            member = found[0]
            target = Path(tmp) / member
        else:
            raise MfFormatError(f"{archive.name}: nie jest archiwum ZIP ani 7z")
        with target.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            header = dbf.read_header(mm[: 64 * 1024])
            yield mm, header, member


def scan(
    mm: mmap.mmap, header: dbf.Header, names: tuple[str, ...]
) -> Iterator[tuple[int, list[str]]]:
    """(record index, raw values of the selected fields) for non-deleted records."""
    offsets = {}
    pos = 1
    for f in header.fields:
        offsets[f.name] = (pos, pos + f.length)
        pos += f.length
    missing = [n for n in names if n not in offsets]
    if missing:
        raise MfFormatError(f"brak pól {missing}")
    spans = [offsets[n] for n in names]
    rl, start = header.record_length, header.header_length
    for i in range(header.record_count):
        base = start + i * rl
        record = mm[base : base + rl]
        if len(record) < rl:
            break
        if record[:1] == b"*":
            continue
        try:
            values = [record[a:b].decode("cp1250").strip() for a, b in spans]
        except UnicodeDecodeError as exc:
            raise MfFormatError(f"rekord {i}: znaki spoza cp1250 ({exc})") from exc
        yield i, values


def num(text: str, where: str = "") -> Decimal:
    if not text:
        return ZERO
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise MfFormatError(f"{where}: {text!r} nie jest liczbą") from exc


def rok_z_pola(text: str) -> int:
    """ROK in MF files has two digits (25 = 2025); a four-digit value is taken as is."""
    if not text.isdigit() or len(text) not in (2, 4):
        raise MfFormatError(f"ROK: {text!r}")
    return 2000 + int(text) if len(text) == 2 else int(text)


class Okres:
    """One period (ROK, KWARTAL) per file; a different period in any record is an error."""

    def __init__(self) -> None:
        self.value: tuple[str, str] | None = None

    def check(self, rok: str, kwartal: str) -> None:
        if self.value is None:
            self.value = (rok, kwartal)
        elif self.value != (rok, kwartal):
            raise MfFormatError(f"różne okresy w pliku: {self.value} i {(rok, kwartal)}")

    @property
    def rok(self) -> int | None:
        return rok_z_pola(self.value[0]) if self.value else None

    @property
    def kwartal(self) -> str | None:
        return self.value[1] if self.value else None


class Klucze:
    """One gmina code comes from one MF unit key (e.g. a gmina record and a city-with-powiat-rights
    record cannot map to the same code)."""

    def __init__(self, report: str) -> None:
        self.report = report
        self.keys: dict[str, tuple[str, ...]] = {}

    def check(self, teryt: str, key: tuple[str, ...]) -> None:
        known = self.keys.setdefault(teryt, key)
        if known != key:
            raise MfFormatError(f"{self.report}: kod {teryt} z dwóch jednostek {known} i {key}")


@dataclass
class Sumy:
    """Report item sums for one gmina."""

    plan: Decimal = ZERO
    wykonanie: Decimal = ZERO
    plan_majatkowe: Decimal = ZERO
    wykonanie_majatkowe: Decimal = ZERO
    wiersze: int = 0
    dzialy: dict[str, list[Decimal]] = field(default_factory=dict)  # dział → [plan, execution]


@dataclass
class Plik:
    """Result of parsing one report: sums per gmina and source description."""

    report: str
    member: str
    rok: int | None
    kwartal: str | None
    gminy: dict[str, Sumy]


def sumy_rb(archive: Path, report: str) -> Plik:
    """Plan and execution sums of Rb-27S or Rb-28S for each gmina, also by dział (division);
    for Rb-28S capital expenditure (groups 16xx) separately."""
    if report not in ("Rb27S", "Rb28S"):
        raise ValueError(report)
    rb28 = report == "Rb28S"
    names = (*KEY_FIELDS, *PERIOD_FIELDS, "DZIAL", "R1", "R4", *(("GRUPA",) if rb28 else ()))
    out: dict[str, Sumy] = defaultdict(Sumy)
    okres, klucze = Okres(), Klucze(report)
    with dbf_member(archive, report) as (mm, header, member):
        for i, values in scan(mm, header, names):
            teryt = gmina_teryt(*values[:5])
            if teryt is None:
                continue
            klucze.check(teryt, tuple(values[:5]))
            okres.check(values[5], values[6])
            dzial, r1, r4 = values[7:10]
            plan = num(r1, f"{report} rekord {i} R1")
            wyk = num(r4, f"{report} rekord {i} R4")
            s = out[teryt]
            s.plan += plan
            s.wykonanie += wyk
            s.wiersze += 1
            d = s.dzialy.setdefault(dzial, [ZERO, ZERO])
            d[0] += plan
            d[1] += wyk
            if rb28 and values[10].startswith(GRUPA_MAJATKOWE):
                s.plan_majatkowe += plan
                s.wykonanie_majatkowe += wyk
    return Plik(report, member, okres.rok, okres.kwartal, dict(out))


@dataclass
class Nds:
    gminy: dict[str, dict[str, Decimal]]
    member: str
    rok: int | None
    kwartal: str | None


def rb_nds(archive: Path) -> Nds:
    """Rb-NDS fields (NDS_FIELDS) for each gmina. Two records for one gmina are an error."""
    out: dict[str, dict[str, Decimal]] = {}
    okres, klucze = Okres(), Klucze("RbNDS")
    with dbf_member(archive, "RbNDS") as (mm, header, member):
        for i, values in scan(mm, header, (*KEY_FIELDS, *PERIOD_FIELDS, *NDS_FIELDS)):
            teryt = gmina_teryt(*values[:5])
            if teryt is None:
                continue
            klucze.check(teryt, tuple(values[:5]))
            okres.check(values[5], values[6])
            if teryt in out:
                raise MfFormatError(f"Rb-NDS: dwa rekordy gminy {teryt}")
            out[teryt] = {
                k: num(v, f"RbNDS rekord {i} {k}")
                for k, v in zip(NDS_FIELDS, values[7:], strict=True)
            }
    return Nds(out, member, okres.rok, okres.kwartal)


@dataclass(frozen=True)
class Kontrola:
    teryt: str
    nazwa: str
    rb: Decimal
    nds: Decimal | None
    pole_nds: str

    @property
    def zgodna(self) -> bool:
        return self.nds is not None and abs(self.rb - self.nds) <= TOLERANCE


def bilansuj(
    rb27: dict[str, Sumy], rb28: dict[str, Sumy], nds: dict[str, dict[str, Decimal]]
) -> list[Kontrola]:
    """Checks from the KONTROLE table: sum of Rb-27S = Rb-NDS A, sum of Rb-28S = Rb-NDS B,
    Rb-28S groups 16xx = Rb-NDS B2 (plan and execution). A mismatch blocks publication."""
    checks = []
    for teryt in sorted(set(rb27) | set(rb28) | set(nds)):
        n = nds.get(teryt)
        for k in KONTROLE:
            sumy = (rb27 if k.report == "Rb27S" else rb28).get(teryt, Sumy())
            value = getattr(sumy, k.attr)
            checks.append(
                Kontrola(teryt, k.kod, value, n.get(k.pole_nds) if n else None, k.pole_nds)
            )
    return checks
