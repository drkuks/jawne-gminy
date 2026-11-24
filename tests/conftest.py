from __future__ import annotations

from pathlib import Path

import pytest

from jg.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(root=tmp_path)
