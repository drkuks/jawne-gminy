"""Project HTTP client (ported from okiemnaolawe `ingest/http.py`, simplified).

Every request, including retries and redirects, goes through the per-domain limit and is
logged. Always the project's explicit User-Agent: the fetching policy forbids browser
headers and circumventing access controls.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx

from jg.config import Settings
from jg.fetch.archive import Archive, ArchivedFile
from jg.fetch.cache import ConditionalCache
from jg.fetch.ratelimit import DomainRateLimiter
from jg.fetch.reqlog import RequestLog, now_local

BACKOFF_S = (5.0, 10.0, 20.0)
MAX_RETRY_AFTER_S = 300.0
MAX_REDIRECTS = 5
RETRY_STATUSES = {429, 502, 503, 504}
CACHE_BODY_MAX = 1_000_000


def backoff(attempt: int) -> float:
    return BACKOFF_S[min(attempt, len(BACKOFF_S)) - 1]


class FetchError(Exception):
    """Fetch failed (status other than 200 after retries, or a network error)."""


def resolve_host(host: str) -> list[str]:
    try:
        return [info[4][0] for info in socket.getaddrinfo(host, None)]
    except (OSError, UnicodeError):
        return []


def blocked_target(url: str, resolver: Callable[[str], list[str]] = resolve_host) -> str | None:
    """Reason for refusal, or None. Only http(s) to public addresses is allowed."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return f"schemat {parts.scheme or 'brak'} niedozwolony"
    host = parts.hostname or ""
    if not host:
        return "brak nazwy hosta"
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        addresses = resolver(host)
        if not addresses:
            return f"nie można ustalić adresu {host}"
    for address in addresses:
        if not ipaddress.ip_address(address.split("%")[0]).is_global:
            return f"adres niepubliczny {host}"
    return None


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        delta = parsedate_to_datetime(value) - now_local()
    except (TypeError, ValueError):
        return None
    return max(0.0, delta.total_seconds())


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int | None
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""
    sha256: str | None = None
    from_cache: bool = False
    attempts: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200

    @property
    def content_type(self) -> str | None:
        return self.headers.get("content-type")

    @property
    def text(self) -> str:
        ctype = self.content_type or ""
        charset = "utf-8"
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";")[0].strip().strip("\"'")
        try:
            return self.content.decode(charset, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    def json(self) -> object:
        import json

        return json.loads(self.content)


class HttpClient:
    def __init__(
        self,
        settings: Settings,
        *,
        source_id: str,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        resolver: Callable[[str], list[str]] = resolve_host,
    ) -> None:
        self.settings = settings
        self.source_id = source_id
        self._clock = clock or time.time
        self._sleep = sleep or time.sleep
        self._resolver = resolver
        self.limiter = DomainRateLimiter(
            settings.var_dir / "ratelimit.json",
            settings.min_interval_s,
            clock=self._clock,
            sleep=self._sleep,
        )
        self.cache = ConditionalCache(settings.var_dir / "http-cache.sqlite")
        self.log = RequestLog(settings.logs_dir / "requests.jsonl")
        self.archive = Archive(settings.archive_dir)
        self._client = httpx.Client(
            timeout=settings.timeout_s, follow_redirects=False, transport=transport
        )
        self.request_count = 0

    def close(self) -> None:
        self._client.close()
        self.cache.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        base = {
            "User-Agent": self.settings.user_agent,
            "Accept": "*/*",
            "Accept-Language": "pl-PL,pl;q=0.9",
        }
        for key, value in (extra or {}).items():
            if key.lower() != "user-agent":
                base[key] = value
        return base

    def get(
        self, url: str, *, headers: dict[str, str] | None = None, use_cache: bool = True
    ) -> FetchResult:
        """GET following redirects; every hop goes through the limit and the log."""
        original = url
        origin_host = urlsplit(url).hostname
        attempts = 0
        result = FetchResult(url, url, None)
        for _ in range(MAX_REDIRECTS + 1):
            # Caller headers (e.g. an API key) are sent only to the originally requested host.
            extra = (headers or {}) if urlsplit(url).hostname == origin_host else {}
            result = self._get_once(url, extra, use_cache)
            attempts += result.attempts
            location = result.headers.get("location")
            if result.status in (301, 302, 303, 307, 308) and location:
                url = urljoin(url, location)
                continue
            break
        result.url = original
        result.attempts = attempts
        return result

    def fetch_to_archive(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        note: str | None = None,
        use_cache: bool = True,
    ) -> ArchivedFile:
        """Fetch a file and store it in the SHA-256 archive. On 304 return the archived entry.

        `use_cache=False` for servers that mishandle conditional requests (BDL)."""
        result = self.get(url, headers=headers, use_cache=use_cache)
        if not result.ok:
            raise FetchError(f"{url}: kod {result.status} {result.error or ''}".strip())
        if result.from_cache:
            known = self.archive.latest(url)
            if known and known.sha256 == result.sha256:
                return known
        return self.archive.store(
            result.content,
            url=url,
            source_id=self.source_id,
            content_type=result.content_type,
            note=note,
        )

    def _get_once(self, url: str, extra: dict[str, str], use_cache: bool) -> FetchResult:
        reason = blocked_target(url, self._resolver)
        if reason:
            self._log(url, None, 0, False, 1, f"odmowa: {reason}")
            return FetchResult(url, url, None, attempts=1, error=f"odmowa: {reason}")
        host = urlsplit(url).hostname or ""
        headers = self.headers(extra)
        cached = self.cache.get(url) if use_cache else None
        if cached:
            headers.update(ConditionalCache.headers_for(cached))

        for attempt in range(1, self.settings.max_attempts + 1):
            self.limiter.wait(host)
            self.request_count += 1
            try:
                resp = self._client.get(url, headers=headers)
            except httpx.TransportError as exc:
                error = f"{type(exc).__name__}: {exc}"
                self._log(url, None, 0, False, attempt, error)
                if attempt < self.settings.max_attempts:
                    self._sleep(backoff(attempt))
                    continue
                return FetchResult(url, url, None, attempts=attempt, error=error)

            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            content = resp.content
            from_cache = resp.status_code == 304 and cached is not None
            self._log(url, resp.status_code, len(content), from_cache, attempt)

            if resp.status_code in RETRY_STATUSES and attempt < self.settings.max_attempts:
                retry_after = parse_retry_after(resp_headers.get("retry-after")) or 0.0
                if retry_after > MAX_RETRY_AFTER_S:
                    # The server asks for a longer pause: stop rather than retry early.
                    return FetchResult(
                        url,
                        str(resp.url),
                        resp.status_code,
                        resp_headers,
                        content,
                        attempts=attempt,
                        error=f"Retry-After {retry_after:.0f} s",
                    )
                self._sleep(max(backoff(attempt), retry_after))
                continue

            if from_cache:
                assert cached is not None
                body = cached.content or self.archive.get(cached.sha256)
                if body is None:
                    # Body is in neither the cache nor the archive: fetch unconditionally.
                    self.cache.delete(url)
                    return self._get_once(url, extra, use_cache=False)
                return FetchResult(
                    url,
                    str(resp.url),
                    200,
                    {**resp_headers, "content-type": cached.content_type or ""},
                    body,
                    cached.sha256,
                    True,
                    attempt,
                )

            sha = hashlib.sha256(content).hexdigest()
            if resp.status_code == 200 and use_cache:
                self.cache.store(
                    url,
                    etag=resp_headers.get("etag"),
                    last_modified=resp_headers.get("last-modified"),
                    content_type=resp_headers.get("content-type"),
                    sha256=sha,
                    # Large files are kept only in the SHA-256 archive, not duplicated in the cache.
                    content=content if len(content) <= CACHE_BODY_MAX else b"",
                )
            return FetchResult(
                url, str(resp.url), resp.status_code, resp_headers, content, sha, False, attempt
            )
        raise AssertionError("unreachable")

    def _log(
        self,
        url: str,
        status: int | None,
        size: int,
        from_cache: bool,
        attempt: int,
        error: str | None = None,
    ) -> None:
        self.log.write(
            source_id=self.source_id,
            method="http",
            url=url,
            user_agent=self.settings.user_agent,
            status=status,
            bytes=size,
            from_cache=from_cache,
            attempt=attempt,
            error=error,
        )
