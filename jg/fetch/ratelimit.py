"""Per-domain request rate limit, persisted between runs (ported from okiemnaolawe).

The limit key is the registered domain, not the full host: bdl.stat.gov.pl and api.stat.gov.pl
share one limit (stat.gov.pl).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import time
from collections.abc import Callable
from pathlib import Path

# Second-level suffixes under which domains are registered (subset of the Public Suffix List).
PUBLIC_SUFFIXES = {"gov.pl", "com.pl", "org.pl", "net.pl", "edu.pl", "info.pl", "co.uk"}


def domain_key(host: str) -> str:
    """Registered domain of a host: `bdl.stat.gov.pl` → `stat.gov.pl`, `www.gov.pl` → `gov.pl`."""
    labels = host.lower().rstrip(".").split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in PUBLIC_SUFFIXES:
        return ".".join(labels[-3:]) if labels[-3] != "www" else ".".join(labels[-2:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else host.lower()


class DomainRateLimiter:
    """Enforces a minimum interval between requests to the same domain (host)."""

    def __init__(
        self,
        state_file: Path | None,
        min_interval_s: float,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.state_file = state_file
        self.min_interval_s = min_interval_s
        self._clock = clock or time.time
        self._sleep = sleep or time.sleep
        self._intervals: dict[str, float] = {}
        self._last: dict[str, float] = {}

    def set_interval(self, host: str, seconds: float) -> None:
        """Lengthen a domain's interval (e.g. robots.txt Crawl-delay); never below the minimum."""
        self._intervals[domain_key(host)] = max(self.min_interval_s, seconds)

    def interval(self, host: str) -> float:
        return self._intervals.get(domain_key(host), self.min_interval_s)

    def wait(self, host: str) -> float:
        """Wait until a request to `host` is allowed. Returns the time waited, in seconds."""
        host = domain_key(host)
        with self._locked():
            self._load()
            waited = 0.0
            last = self._last.get(host)
            if last is not None:
                remaining = self.interval(host) - (self._clock() - last)
                if remaining > 0:
                    self._sleep(remaining)
                    waited = remaining
            self._last[host] = self._clock()
            self._save()
        return waited

    @contextlib.contextmanager
    def _locked(self):
        """Lock the state file during check and write, so two processes cannot send requests
        to the same domain closer together than the limit."""
        if not self.state_file:
            yield
            return
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.state_file.with_suffix(".lock")
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _load(self) -> None:
        if self.state_file and self.state_file.is_file():
            stored = json.loads(self.state_file.read_text(encoding="utf-8"))
            for host, ts in stored.items():
                self._last[host] = max(ts, self._last.get(host, 0.0))

    def _save(self) -> None:
        if self.state_file:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(self._last, indent=1), encoding="utf-8")
