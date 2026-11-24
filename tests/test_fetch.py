from __future__ import annotations

import json

import httpx
import pytest

from jg.fetch import HttpClient
from jg.fetch.client import FetchError, blocked_target, parse_retry_after
from jg.fetch.ratelimit import DomainRateLimiter, domain_key


def public_resolver(host: str) -> list[str]:
    return ["93.184.216.34"]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(settings, handler):
    clock = FakeClock()
    client = HttpClient(
        settings,
        source_id="S1",
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleep=clock.sleep,
        resolver=public_resolver,
    )
    return client, clock


def read_log(settings):
    path = settings.logs_dir / "requests.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize(
    ("host", "key"),
    [
        ("bdl.stat.gov.pl", "stat.gov.pl"),
        ("api.stat.gov.pl", "stat.gov.pl"),
        ("www.gov.pl", "gov.pl"),
        ("api-krs.ms.gov.pl", "ms.gov.pl"),
        ("mapy.geoportal.gov.pl", "geoportal.gov.pl"),
        ("example.com", "example.com"),
        ("a.b.example.com", "example.com"),
    ],
)
def test_domain_key(host, key):
    assert domain_key(host) == key


def test_rate_limit_shared_by_subdomains():
    clock = FakeClock()
    limiter = DomainRateLimiter(None, 5.0, clock=clock, sleep=clock.sleep)
    limiter.wait("bdl.stat.gov.pl")
    clock.now += 1
    assert limiter.wait("api.stat.gov.pl") == pytest.approx(4.0)
    assert limiter.wait("www.gov.pl") == 0


def test_get_logs_user_agent_and_status(settings):
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, content=b"dane", headers={"content-type": "text/plain"})

    client, _ = make_client(settings, handler)
    result = client.get("https://www.gov.pl/plik")
    assert result.ok and result.content == b"dane"
    assert seen["ua"].startswith("JawneGminy-bot/")
    (entry,) = read_log(settings)
    assert entry["status"] == 200
    assert entry["user_agent"] == seen["ua"]
    assert entry["method"] == "http"
    assert entry["source_id"] == "S1"
    assert "ts" in entry


def test_user_agent_cannot_be_overridden(settings):
    def handler(request):
        return httpx.Response(200, content=request.headers["user-agent"].encode())

    client, _ = make_client(settings, handler)
    result = client.get("https://www.gov.pl/", headers={"User-Agent": "Mozilla/5.0"})
    assert result.content.startswith(b"JawneGminy-bot/")


def test_second_request_waits_five_seconds(settings):
    client, clock = make_client(settings, lambda r: httpx.Response(200, content=b"x"))
    client.get("https://www.gov.pl/a")
    client.get("https://www.gov.pl/b")
    assert clock.sleeps == [pytest.approx(5.0)]


def test_redirect_goes_through_limit_and_log(settings):
    def handler(request):
        if request.url.path == "/a":
            return httpx.Response(302, headers={"location": "https://files.stat.gov.pl/b"})
        return httpx.Response(200, content=b"ok")

    client, clock = make_client(settings, handler)
    result = client.get("https://bdl.stat.gov.pl/a")
    assert result.ok and result.final_url == "https://files.stat.gov.pl/b"
    assert [e["url"] for e in read_log(settings)] == [
        "https://bdl.stat.gov.pl/a",
        "https://files.stat.gov.pl/b",
    ]
    assert clock.sleeps == [pytest.approx(5.0)]  # same domain stat.gov.pl


def test_conditional_cache_304(settings):
    calls = []

    def handler(request):
        calls.append(dict(request.headers))
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=b"tresc", headers={"etag": '"v1"'})

    client, _ = make_client(settings, handler)
    first = client.get("https://bdl.stat.gov.pl/api")
    second = client.get("https://bdl.stat.gov.pl/api")
    assert second.from_cache and second.content == b"tresc"
    assert second.sha256 == first.sha256
    assert calls[1]["if-none-match"] == '"v1"'


def test_retry_on_503_then_ok(settings):
    responses = iter([httpx.Response(503, headers={"retry-after": "7"}), httpx.Response(200)])
    client, clock = make_client(settings, lambda r: next(responses))
    result = client.get("https://api-krs.ms.gov.pl/x")
    assert result.ok and result.attempts == 2
    assert 7.0 in clock.sleeps


def test_blocked_private_target(settings):
    client, _ = make_client(settings, lambda r: httpx.Response(200))
    client._resolver = lambda host: ["127.0.0.1"]
    result = client.get("https://localhost.example/")
    assert result.status is None and "niepubliczny" in result.error
    assert blocked_target("file:///etc/passwd") is not None


def test_fetch_to_archive_stores_by_sha256(settings):
    client, _ = make_client(settings, lambda r: httpx.Response(200, content=b"PK-zip"))
    item = client.fetch_to_archive("https://www.gov.pl/attachment/abc")
    assert item.path.name == item.sha256
    assert item.path.read_bytes() == b"PK-zip"
    assert client.archive.latest("https://www.gov.pl/attachment/abc").sha256 == item.sha256


def test_fetch_to_archive_raises_on_error(settings):
    client, _ = make_client(settings, lambda r: httpx.Response(404))
    with pytest.raises(FetchError):
        client.fetch_to_archive("https://www.gov.pl/brak")


def test_parse_retry_after():
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("nonsens") is None


def test_dns_failure_is_blocked():
    assert "nie można ustalić" in blocked_target("https://brak.invalid/", lambda h: [])


def test_retry_after_above_cap_gives_up(settings):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "3600"})

    client, clock = make_client(settings, handler)
    result = client.get("https://www.gov.pl/x")
    assert result.status == 429 and "Retry-After" in result.error
    assert len(calls) == 1 and clock.sleeps == []


def test_many_attempts_do_not_overflow_backoff(settings):
    from dataclasses import replace

    client, clock = make_client(replace(settings, max_attempts=5), lambda r: httpx.Response(503))
    result = client.get("https://www.gov.pl/x")
    assert result.status == 503 and result.attempts == 5


def test_headers_not_forwarded_to_other_host(settings):
    seen = {}

    def handler(request):
        seen[request.url.host] = request.headers.get("x-clientid")
        if request.url.host == "bdl.stat.gov.pl":
            return httpx.Response(302, headers={"location": "https://inny.example.com/"})
        return httpx.Response(200)

    client, _ = make_client(settings, handler)
    client.get("https://bdl.stat.gov.pl/api", headers={"X-ClientId": "sekret"})
    assert seen == {"bdl.stat.gov.pl": "sekret", "inny.example.com": None}


def test_quoted_charset():
    from jg.fetch.client import FetchResult

    r = FetchResult(
        "u",
        "u",
        200,
        {"content-type": 'text/html; charset="windows-1250"'},
        "Oława".encode("cp1250"),
    )
    assert r.text == "Oława"


def test_large_body_served_from_archive_on_304(settings, monkeypatch):
    from jg.fetch import client as client_mod

    monkeypatch.setattr(client_mod, "CACHE_BODY_MAX", 3)

    def handler(request):
        if request.headers.get("if-none-match"):
            return httpx.Response(304)
        return httpx.Response(200, content=b"duzy plik", headers={"etag": '"e"'})

    client, _ = make_client(settings, handler)
    first = client.fetch_to_archive("https://www.gov.pl/a")
    assert client.cache.get("https://www.gov.pl/a").content == b""
    second = client.get("https://www.gov.pl/a")
    assert second.from_cache and second.content == b"duzy plik"
    assert second.sha256 == first.sha256


def test_limiter_state_shared_between_instances(tmp_path):
    clock = FakeClock()
    state = tmp_path / "rl.json"
    a = DomainRateLimiter(state, 5.0, clock=clock, sleep=clock.sleep)
    b = DomainRateLimiter(state, 5.0, clock=clock, sleep=clock.sleep)
    a.wait("www.gov.pl")
    assert b.wait("www.gov.pl") == pytest.approx(5.0)
