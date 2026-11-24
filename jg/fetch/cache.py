"""Conditional request cache (ETag / Last-Modified) in SQLite (ported from okiemnaolawe).

Large file bodies are not stored here (left empty); they live only in the SHA-256 archive.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CacheEntry:
    url: str
    etag: str | None
    last_modified: str | None
    content_type: str | None
    sha256: str
    content: bytes


class ConditionalCache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS response ("
            " url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, content_type TEXT,"
            " sha256 TEXT NOT NULL, content BLOB NOT NULL, stored_at TEXT NOT NULL)"
        )

    def get(self, url: str) -> CacheEntry | None:
        row = self._db.execute(
            "SELECT url, etag, last_modified, content_type, sha256, content"
            " FROM response WHERE url = ?",
            (url,),
        ).fetchone()
        return CacheEntry(*row) if row else None

    def conditional_headers(self, url: str) -> dict[str, str]:
        entry = self.get(url)
        return {} if entry is None else self.headers_for(entry)

    @staticmethod
    def headers_for(entry: CacheEntry) -> dict[str, str]:
        headers = {}
        if entry.etag:
            etag = entry.etag.strip()
            # RFC 9110: ETag is quoted. BDL returns it unquoted, and sending it back in that
            # form makes the server drop the connection; quoted, it works.
            if not (etag.startswith('"') or etag.startswith('W/"')):
                etag = f'"{etag}"'
            headers["If-None-Match"] = etag
        if entry.last_modified:
            headers["If-Modified-Since"] = entry.last_modified
        return headers

    def store(
        self,
        url: str,
        *,
        etag: str | None,
        last_modified: str | None,
        content_type: str | None,
        sha256: str,
        content: bytes,
    ) -> None:
        """Store the response if it has a validator (caching needs ETag or Last-Modified)."""
        if not etag and not last_modified:
            return
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO response VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                (url, etag, last_modified, content_type, sha256, content),
            )

    def delete(self, url: str) -> None:
        with self._db:
            self._db.execute("DELETE FROM response WHERE url = ?", (url,))

    def close(self) -> None:
        self._db.close()
