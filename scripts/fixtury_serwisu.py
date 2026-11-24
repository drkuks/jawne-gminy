"""Subset of real `data/` for the site tests (tests/fixtures/site/).

Usage: uv run python scripts/fixtury_serwisu.py
Kept: selected gminy (Oława, Domaniów, Jelcz-Laskowice, Wrocław, Szczawa) plus 6 gminy from each
comparison group of Oława and Domaniów (so groups have at least min_n gminy). Company files as is.
"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

DATA = Path("data")
OUT = Path("tests/fixtures/site")
WYBRANE = {"0215011", "0215022", "0215033", "0264011", "1207132"}


def rows(name: str) -> tuple[list[str], list[dict]]:
    with (DATA / name).open(encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        return r.fieldnames, list(r)


def write(name: str, header: list[str], data: list[dict]) -> None:
    with (OUT / name).open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, header)
        w.writeheader()
        w.writerows(data)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    h, por = rows("porownania.csv")
    grupy = {r["grupa"] for r in por if r["teryt"] in WYBRANE and r["rok"] == "2025"}
    teryty = set(WYBRANE)
    for g in sorted(grupy):
        teryty |= set(sorted({r["teryt"] for r in por if r["grupa"] == g})[:6])
    write("porownania.csv", h, [r for r in por if r["teryt"] in teryty])
    for name in ("gminy.csv", "wartosci.csv"):
        h, data = rows(name)
        write(name, h, [r for r in data if r["teryt"] in teryty])
    for name in ("spolki.csv", "spolki_wspolnicy.csv", "spolki_finanse.csv"):
        shutil.copy(DATA / name, OUT / name)
    print(f"{OUT}: {len(teryty)} gmin")


if __name__ == "__main__":
    main()
