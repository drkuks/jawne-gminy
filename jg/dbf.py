"""Minimal DBF reader (dBase III/IV, FoxPro without memo fields) for MF databases
(from okiemnaolawe).

No external dependencies: 32-byte header (version, date, record count, header and record
length), 32-byte field descriptors up to the `0x0D` terminator, fixed-length records (first
byte `*` marks a deleted record). Types: `C` text, `N`/`F` number (decimal text → `Decimal`),
`D` date `YYYYMMDD`, `L` logical. Text is cp1250 (MF files).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

HEADER_LEN = 32
FIELD_LEN = 32
FIELD_TERMINATOR = 0x0D


class DbfError(Exception):
    pass


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    length: int
    decimals: int


@dataclass(frozen=True)
class Header:
    record_count: int
    header_length: int
    record_length: int
    fields: tuple[Field, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)


def read_header(data: bytes) -> Header:
    if len(data) < HEADER_LEN:
        raise DbfError("plik krótszy niż nagłówek DBF")
    record_count, header_length, record_length = struct.unpack("<IHH", data[4:12])
    fields: list[Field] = []
    pos = HEADER_LEN
    while pos < len(data) and data[pos] != FIELD_TERMINATOR:
        if pos + FIELD_LEN > len(data):
            raise DbfError("ucięty opis pól DBF")
        descriptor = data[pos : pos + FIELD_LEN]
        name = descriptor[:11].split(b"\x00", 1)[0].decode("ascii", "replace")
        fields.append(Field(name, chr(descriptor[11]), descriptor[16], descriptor[17]))
        pos += FIELD_LEN
    if not fields:
        raise DbfError("brak opisów pól DBF")
    if 1 + sum(f.length for f in fields) != record_length:
        raise DbfError("suma długości pól nie zgadza się z długością rekordu")
    return Header(record_count, header_length, record_length, tuple(fields))
