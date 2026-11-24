"""Configuration: paths, bot User-Agent, HTTP client constants. Secrets only from `.env`."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MIN_INTERVAL_S = 5.0
MAX_ATTEMPTS = 3
TIMEOUT_S = 60.0
BOT_NAME = "JawneGminy-bot"
BOT_VERSION = "0.1"
BOT_URL = "https://github.com/drkuks/jawne-gminy"


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


@dataclass(frozen=True)
class Settings:
    root: Path
    min_interval_s: float = MIN_INTERVAL_S
    max_attempts: int = MAX_ATTEMPTS
    timeout_s: float = TIMEOUT_S
    gus_bdl_key: str | None = None
    regon_bir_key: str | None = None

    @property
    def var_dir(self) -> Path:
        """Working state (cache, rate limits, logs); kept out of the repository."""
        return self.root / "var"

    @property
    def archive_dir(self) -> Path:
        """Source files stored under their SHA-256 name."""
        return self.root / "archive"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def logs_dir(self) -> Path:
        return self.var_dir / "logs"

    @property
    def user_agent(self) -> str:
        return f"{BOT_NAME}/{BOT_VERSION} (+{BOT_URL})"

    @classmethod
    def load(cls, root: Path | None = None) -> Settings:
        root = (root or Path(os.environ.get("JG_ROOT", Path.cwd()))).resolve()
        env = {**read_dotenv(root / ".env"), **os.environ}
        return cls(
            root=root,
            min_interval_s=max(MIN_INTERVAL_S, float(env.get("JG_MIN_INTERVAL_S", MIN_INTERVAL_S))),
            gus_bdl_key=env.get("GUS_BDL_KEY") or None,
            regon_bir_key=env.get("REGON_BIR_KEY") or None,
        )
