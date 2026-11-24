"""Source file archive: each file stored under its SHA-256 name, metadata in a JSONL manifest."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from jg.fetch.reqlog import now_local


@dataclass(frozen=True)
class ArchivedFile:
    sha256: str
    path: Path
    url: str
    source_id: str
    fetched_at: str
    size: int
    content_type: str | None = None
    note: str | None = None

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()


class Archive:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifest = root / "manifest.jsonl"
        root.mkdir(parents=True, exist_ok=True)

    def path_for(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256

    def store(
        self,
        content: bytes,
        *,
        url: str,
        source_id: str,
        content_type: str | None = None,
        note: str | None = None,
        fetched_at: str | None = None,
    ) -> ArchivedFile:
        """Store the content (unless already present) and append an entry to the manifest.

        `fetched_at`: fetch date supplied by the caller (manually downloaded files). If the
        manifest already has the same URL and hash, the existing entry is returned (no new line)."""
        sha = hashlib.sha256(content).hexdigest()
        path = self.path_for(sha)
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(content)
            tmp.replace(path)
        if fetched_at is not None:
            known = self.latest(url)
            if known is not None and known.sha256 == sha:
                return known
        item = ArchivedFile(
            sha256=sha,
            path=path,
            url=url,
            source_id=source_id,
            fetched_at=fetched_at or now_local().isoformat(timespec="seconds"),
            size=len(content),
            content_type=content_type,
            note=note,
        )
        record = {k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(item).items()}
        record["path"] = str(path.relative_to(self.root))
        with self.manifest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return item

    def get(self, sha256: str) -> bytes | None:
        path = self.path_for(sha256)
        return path.read_bytes() if path.is_file() else None

    def latest(self, url: str) -> ArchivedFile | None:
        """Most recent archived file fetched from `url` (according to the manifest)."""
        if not self.manifest.is_file():
            return None
        found = None
        for line in self.manifest.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec["url"] == url:
                found = rec
        if found is None:
            return None
        found["path"] = self.root / found["path"]
        return ArchivedFile(**found)
