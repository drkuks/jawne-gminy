"""Gmina (municipality) dictionary: TERYT codes and type from the MF Słownik JST, name and
point from PRG (GUGiK).

Sources (sources.yaml):
- S1, MF Słownik JST (XLSX): columns Nazwa, WK, PK, GK, GT, PT, Kod GUS, Kod MSWiA, Typ Jst Nazwa.
  Gminy: GT = 1 (urban), 2 (rural), 3 (urban-rural), PT = 0. Cities with powiat rights are
  recorded as a powiat (GK = 00, PT = 2); their TERYT gmina code is WK PK 01 1.
  JST unions (GT = Z) are skipped.
- S4, PRG WFS, layer A03_Granice_gmin: JPT_KOD_JE (7-character TERYT), JPT_NAZWA_ (name),
  REGON, geometry in EPSG:2180 (axis order: x = north, y = east).
"""

from __future__ import annotations

import copy
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

import openpyxl
from lxml import etree
from pyproj import Transformer
from shapely.geometry import Polygon

from jg.teryt import TYPY, gmina_teryt, is_mnpp

NS = {
    "wfs": "http://www.opengis.net/wfs/2.0",
    "gml": "http://www.opengis.net/gml/3.2",
    "ms": "http://mapserver.gis.umn.edu/mapserver",
}
_TO_WGS84 = Transformer.from_crs("EPSG:2180", "EPSG:4326")  # EPSG axis order: (x, y) → (lat, lon)


@dataclass(frozen=True)
class MfJst:
    nazwa: str
    wk: str
    pk: str
    gk: str
    gt: str
    pt: str
    typ_nazwa: str


BRAK_UWAG = "brak uwag"  # `uwagi` field in data/gminy.csv when there is no note (never empty)


class SourceFormatError(Exception):
    """Source file has an unexpected format (missing column, WFS error response)."""


@dataclass(frozen=True)
class PrgGmina:
    teryt: str
    nazwa: str
    regon: str | None
    lat: float
    lon: float
    zrodlo_sha256: str | None = None


@dataclass
class Gmina:
    teryt: str
    nazwa: str
    typ: str
    mnpp: bool
    powiat_teryt: str
    woj_teryt: str
    nazwa_mf: str
    regon: str | None = None
    lat: float | None = None
    lon: float | None = None
    zrodlo_prg_sha256: str | None = None
    uwagi: list[str] = field(default_factory=list)


def _code(value: object) -> str:
    """Code from a cell: numbers stored as float (e.g. 2.0) without the decimal part."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def read_mf_slownik(content: bytes) -> list[MfJst]:
    """Rows of the MF Słownik JST (XLSX)."""
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    try:
        rows = wb.worksheets[0].iter_rows(values_only=True)
        header = [_code(c).lower() for c in next(rows)]
        idx = {}
        for name in ("nazwa", "wk", "pk", "gk", "gt", "pt"):
            if name not in header:
                raise SourceFormatError(f"Słownik JST: brak kolumny {name.upper()} w {header}")
            idx[name] = header.index(name)
        typ_col = next((i for i, h in enumerate(header) if h.startswith("typ")), None)
        out = []
        for row in rows:
            if not row or _code(row[idx["wk"]]) == "":
                continue
            out.append(
                MfJst(
                    nazwa=_code(row[idx["nazwa"]]),
                    wk=_code(row[idx["wk"]]).zfill(2),
                    pk=_code(row[idx["pk"]]).zfill(2),
                    gk=_code(row[idx["gk"]]).zfill(2),
                    gt=_code(row[idx["gt"]]).upper(),
                    pt=_code(row[idx["pt"]]),
                    typ_nazwa=_code(row[typ_col]) if typ_col is not None else "",
                )
            )
        return out
    finally:
        wb.close()


def gminy_z_mf(rows: Iterable[MfJst]) -> dict[str, Gmina]:
    """Gminy (including cities with powiat rights) by TERYT. JST unions are skipped."""
    out: dict[str, Gmina] = {}
    for r in rows:
        teryt = gmina_teryt(r.wk, r.pk, r.gk, r.gt, r.pt)
        if teryt is None:
            continue
        if teryt in out:
            raise SourceFormatError(f"Słownik JST: dwa wiersze dla gminy {teryt}")
        mnpp = is_mnpp(r.gk, r.gt, r.pt)
        typ = "miejska" if mnpp else TYPY[r.gt]
        out[teryt] = Gmina(teryt, r.nazwa, typ, mnpp, r.wk + r.pk, r.wk, r.nazwa)
    return out


def _coords(pos_list: etree._Element) -> list[tuple[float, float]]:
    values = [float(v) for v in (pos_list.text or "").split()]
    return list(zip(values[0::2], values[1::2], strict=True))


def _polygons(member: etree._Element) -> list[Polygon]:
    """Feature polygons with holes (gml:interior), e.g. a rural gmina surrounding a town."""
    out = []
    for poly in member.iterfind(".//gml:Polygon", NS):
        ext = poly.find("gml:exterior//gml:posList", NS)
        if ext is None:
            continue
        holes = [_coords(p) for p in poly.iterfind("gml:interior//gml:posList", NS)]
        out.append(Polygon(_coords(ext), holes))
    return out


def punkt(polygons: list[Polygon]) -> tuple[float, float]:
    """Gmina point (lat, lon): centroid of the largest polygon, or, if it falls outside it
    (irregular shape or a hole for a town), an interior point."""
    polygon = max(polygons, key=lambda p: p.area)
    point = polygon.centroid
    if not polygon.contains(point):
        point = polygon.representative_point()
    lat, lon = _TO_WGS84.transform(point.x, point.y)
    return round(lat, 5), round(lon, 5)


def read_prg_gml(
    content: bytes, zrodlo_sha256: str | None = None
) -> tuple[list[PrgGmina], list[str]]:
    """Gminy from a WFS page and a list of skipped features (missing code, name or geometry).

    A WFS error response (ExceptionReport) or any document other than a FeatureCollection
    raises `SourceFormatError` instead of yielding an empty page."""
    root = etree.fromstring(content)
    if etree.QName(root).localname != "FeatureCollection":
        text = " ".join(root.itertext()).strip()[:200]
        raise SourceFormatError(f"WFS: {etree.QName(root).localname}: {text}")
    out, pominiete = [], []
    for member in root.iterfind("wfs:member/ms:A03_Granice_gmin", NS):
        teryt = (member.findtext("ms:JPT_KOD_JE", namespaces=NS) or "").strip()
        nazwa = (member.findtext("ms:JPT_NAZWA_", namespaces=NS) or "").strip()
        regon = (member.findtext("ms:REGON", namespaces=NS) or "").strip()[:9] or None
        polygons = _polygons(member)
        if not teryt or not nazwa or not polygons:
            pominiete.append(f"PRG: obiekt bez kodu, nazwy lub geometrii ({teryt or nazwa})")
            continue
        lat, lon = punkt(polygons)
        out.append(PrgGmina(teryt, nazwa, regon, lat, lon, zrodlo_sha256))
    return out, pominiete


def number_returned(content: bytes) -> int:
    root = etree.fromstring(content)
    value = root.get("numberReturned")
    if value is None or not value.isdigit():
        raise SourceFormatError("WFS: brak numberReturned")
    return int(value)


def polacz(mf: dict[str, Gmina], prg: Iterable[PrgGmina]) -> tuple[list[Gmina], list[str]]:
    """Join MF and PRG on TERYT. Returns new gmina objects and a list of discrepancies."""
    mf = copy.deepcopy(mf)
    rozbieznosci = []
    prg_by = {}
    for p in prg:
        if p.teryt in prg_by:
            rozbieznosci.append(f"PRG: powtórzony kod {p.teryt}")
        prg_by[p.teryt] = p
    uzyte: set[str] = set()
    for teryt, g in mf.items():
        p = prg_by.get(teryt)
        if p is None:
            # Gmina type change (e.g. rural → urban-rural after gaining town rights): PRG has
            # the current state, MF the state of the report year. Same WWPPGG, different type digit.
            kandydaci = [
                q
                for k, q in prg_by.items()
                if k[:6] == teryt[:6] and k not in mf and k not in uzyte and k[6] in TYPY
            ]
            if len(kandydaci) == 1:
                p = kandydaci[0]
                g.uwagi.append(f"w PRG kod {p.teryt} (zmiana typu gminy po roku MF)")
                rozbieznosci.append(f"zmiana typu: MF {teryt} / PRG {p.teryt} {p.nazwa}")
            else:
                g.uwagi.append("brak danych PRG: nazwa z MF, brak punktu")
                rozbieznosci.append(f"brak w PRG: {teryt} {g.nazwa_mf}")
                continue
        uzyte.add(p.teryt)
        g.nazwa, g.regon, g.lat, g.lon = p.nazwa, p.regon, p.lat, p.lon
        g.zrodlo_prg_sha256 = p.zrodlo_sha256
        if _norm(p.nazwa) != _norm(g.nazwa_mf):
            rozbieznosci.append(f"nazwa: {teryt} MF „{g.nazwa_mf}” / PRG „{p.nazwa}”")
            if _skrot(g.nazwa_mf) != _skrot(p.nazwa):
                g.uwagi.append(f"nazwa w Słowniku JST MF: {g.nazwa_mf}")
    for teryt in sorted(set(prg_by) - uzyte):
        rozbieznosci.append(f"brak w MF: {teryt} {prg_by[teryt].nazwa}")
    return sorted(mf.values(), key=lambda g: g.teryt), rozbieznosci


def _norm(name: str) -> str:
    name = re.sub(r"^(m\.\s*st\.\s*|m\.\s*)", "", name.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", name).upper()


def _skrot(name: str) -> str:
    """Name with spelling differences removed (abbreviations "n.", "św.", brackets, hyphens,
    Polish letters in MF)."""
    name = _norm(name)
    for a, b in ((" NAD ", " N. "), ("ŚWIĘTEJ", "ŚW."), ("ŚWIĘTY", "ŚW."), ("-", " ")):
        name = name.replace(a, b)
    name = re.sub(r"[()]", "", name)
    return name.replace("Ę", "E").replace("Ó", "O").replace("Ł", "L")
