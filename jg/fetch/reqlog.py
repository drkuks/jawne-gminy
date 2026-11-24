"""Log of every HTTP request (JSONL): method, User-Agent, time, HTTP status
(required by the project's fetching policy)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Warsaw")


def now_local() -> datetime:
    return datetime.now(TZ)


class RequestLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, **record: object) -> None:
        line = {"ts": now_local().isoformat(timespec="seconds"), **record}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
