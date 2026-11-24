"""Shared site build from the data sample (tests/fixtures/site) for tests."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from jg.site import build

FIXTURES = Path(__file__).parent / "fixtures"


def build_site(tmp: Path, baza: str = "/") -> Path:
    archive = tmp / "archive"
    slownik = (FIXTURES / "mf" / "slownik_jst_2025.xlsx").read_bytes()
    sha = hashlib.sha256(slownik).hexdigest()
    (archive / sha[:2]).mkdir(parents=True)
    (archive / sha[:2] / sha).write_bytes(slownik)
    data = tmp / "data"
    shutil.copytree(FIXTURES / "site", data)
    out = tmp / "dist"
    build.build(data, archive, out, baza=baza)
    return out
