"""Subset of a real MF file for tests: selected DBF records in a new ZIP archive.

Usage: uv run python scripts/wycinek_dbf.py <archive.zip> <Rb27S|Rb28S|RbNDS> <output.zip>
       <gmina teryt> [...] [--inne N]
The DBF header is copied unchanged except for the record count. `--inne N` adds the first N
records of units other than gminy (powiat, województwo, association) for filtering tests.
"""

from __future__ import annotations

import struct
import sys
import zipfile
from pathlib import Path

from jg import mf_rb
from jg.teryt import gmina_teryt


def main(argv: list[str]) -> None:
    src, report, dst, *rest = argv
    inne = 0
    if "--inne" in rest:
        i = rest.index("--inne")
        inne = int(rest[i + 1])
        rest = rest[:i] + rest[i + 2 :]
    wanted = set(rest)
    with mf_rb.dbf_member(Path(src), report) as (mm, header, member):
        chosen = []
        other = 0
        for i, values in mf_rb.scan(mm, header, mf_rb.KEY_FIELDS):
            t = gmina_teryt(*values)
            if t in wanted or (t is None and other < inne):
                other += t is None
                base = header.header_length + i * header.record_length
                chosen.append(mm[base : base + header.record_length])
        head = bytearray(mm[: header.header_length])
    struct.pack_into("<I", head, 4, len(chosen))
    data = bytes(head) + b"".join(chosen) + b"\x1a"
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(Path(member).name, data)
    print(f"{dst}: {len(chosen)} rekordów z {member}")


if __name__ == "__main__":
    main(sys.argv[1:])
