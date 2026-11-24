"""GUS Bank Danych Lokalnych (BDL, S2): gmina population, variable 72305 (total, as of 31 Dec).

API: `GET /api/v1/data/by-variable/72305?unit-level=6&year=...&page-size=100&format=json`
(about 40 pages for all years). Without a key the limit is 100 requests / 15 min, hence a 9.5 s
interval. A BDL unit id (12 digits) is not TERYT: gmina code = id[2:4] + id[7:9] + id[9:11] +
id[11] (from okiemnaolawe `oko/bdl.py`). Level-6 units also include parts of urban-rural gminy
(types 4, 5) and city districts; only codes from the gmina dictionary are kept.

Value attribute (`attrId`): 1, 3, 9, 11, 13, 20, 21, 97 = value; 94, 98 = meaningful zero;
others (0, 4, 7, 14, 15, 17, 50, 91 …) = brak danych, never 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

BDL_HOST = "bdl.stat.gov.pl"
BDL_API = f"https://{BDL_HOST}/api/v1"
BDL_INTERVAL_S = 9.5
KEY_HEADER = "X-ClientId"
ZMIENNA_LUDNOSC = 72305
VALUE_ATTRS = {1, 3, 9, 11, 13, 20, 21, 97}
ZERO_ATTRS = {94, 98}


def teryt(bdl_id: str) -> str | None:
    """7-character gmina TERYT code from a 12-digit BDL unit id."""
    if len(bdl_id) != 12 or not bdl_id.isdigit():
        return None
    return bdl_id[2:4] + bdl_id[7:9] + bdl_id[9:11] + bdl_id[11]


def url_by_variable(var_id: int, years: list[int], page: int, page_size: int = 100) -> str:
    params = [("unit-level", 6), *(("year", y) for y in years)]
    params += [("page-size", page_size), ("page", page), ("format", "json")]
    return f"{BDL_API}/data/by-variable/{var_id}?{urlencode(params)}"


@dataclass(frozen=True)
class Wartosc:
    teryt: str
    rok: int
    wartosc: int | None
    atrybut: int
    nazwa_bdl: str
    strona: int = 0  # index of the response page (source of the value)
    bdl_id: str = ""  # BDL unit id (12 digits)

    @property
    def przyczyna_braku(self) -> str:
        if self.atrybut in VALUE_ATTRS:
            return f"BDL bez wartości (atrybut {self.atrybut})"
        return f"BDL: atrybut {self.atrybut} oznacza brak danych"


def parse_page(data: dict, strona: int = 0) -> list[Wartosc]:
    """Values from a `by-variable` response page."""
    out = []
    for unit in data.get("results", []):
        kod = teryt(unit["id"])
        if kod is None:
            continue
        for v in unit.get("values", []):
            attr = int(v.get("attrId", 0))
            if attr in VALUE_ATTRS and v.get("val") is not None:
                val = int(v["val"])
            elif attr in ZERO_ATTRS:
                val = 0
            else:
                val = None
            out.append(
                Wartosc(kod, int(v["year"]), val, attr, unit.get("name", ""), strona, unit["id"])
            )
    return out


def total_pages(data: dict, page_size: int = 100) -> int:
    total = int(data["totalRecords"])
    return max(1, -(-total // page_size))
