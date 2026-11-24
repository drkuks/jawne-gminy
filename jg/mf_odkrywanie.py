"""Discovery of MF report files (S1) on gov.pl (based on okiemnaolawe `oko/mf.py`).

Path: "Sprawozdania budżetowe" page → year pages ("RRRR r.") → the year's "Bazy danych" page
(the `bazy-danychN` number differs every year) → `/attachment/<uuid>` attachments. Attachment
names differ between years ("Dochody po IV kwartałach 2019r Dochody_IVkw2019.ZIP",
"Dochody po IV kwartałach 2025 r. Rb27S.zip"), so matching uses the start of the link text
and the "po IV kwartałach" (full-year) marker. Only annual files and the Słownik JST are taken.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import urljoin

INDEX_URL = "https://www.gov.pl/web/finanse/sprawozdania-budzetowe"
LINK = re.compile(r'<a\b[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
TAG = re.compile(r"<[^>]+>")
YEAR_TEXT = re.compile(r"^(\d{4})\s*r\.?$")
YEAR_IN_NAME = re.compile(r"(20\d{2})")
IV_KW = re.compile(r"\bpo\s+iv\s+kwarta", re.I)
KINDS = (
    ("dochody", "Rb27S"),
    ("wydatki", "Rb28S"),
    ("nadwyżka i deficyt", "RbNDS"),
    ("zobowiązania", "RbZ"),
)
REPORTS = tuple(k for _, k in KINDS)


class DiscoveryError(Exception):
    pass


@dataclass(frozen=True)
class Attachment:
    url: str
    kind: str  # Rb27S | Rb28S | RbNDS | RbZ | Slownik
    year: int
    title: str


def links(page: str, base_url: str) -> list[tuple[str, str]]:
    """Pairs (absolute URL, link text without tags and zero-width characters)."""
    out = []
    for href, inner in LINK.findall(page):
        text = html.unescape(TAG.sub(" ", inner)).replace("​", "")
        out.append((urljoin(base_url, html.unescape(href)), re.sub(r"\s+", " ", text).strip()))
    return out


def year_pages(index_html: str, base_url: str = INDEX_URL) -> dict[int, str]:
    years: dict[int, str] = {}
    for url, text in links(index_html, base_url):
        m = YEAR_TEXT.match(text)
        if m and "/web/finanse/" in url:
            years.setdefault(int(m.group(1)), url)
    return years


def database_page(year_html: str, base_url: str) -> str | None:
    for url, text in links(year_html, base_url):
        if text.lower() == "bazy danych":
            return url
    return None


def classify(text: str, url: str) -> Attachment | None:
    lower = text.lower()
    y = YEAR_IN_NAME.search(text)
    if not y:
        return None
    if lower.startswith("słownik"):
        return Attachment(url, "Slownik", int(y.group(1)), text)
    kind = next((k for prefix, k in KINDS if lower.startswith(prefix)), None)
    if kind is None or not IV_KW.search(lower):
        return None
    return Attachment(url, kind, int(y.group(1)), text)


def attachments(database_html: str, base_url: str) -> list[Attachment]:
    found, seen = [], set()
    for url, text in links(database_html, base_url):
        if "/attachment/" not in url or url in seen:
            continue
        item = classify(text, url)
        if item:
            found.append(item)
            seen.add(url)
    return found


def roczne(items: list[Attachment], rok: int) -> dict[str, Attachment]:
    """One "po IV kwartałach" file per report for the year; two different ones are an error."""
    out: dict[str, Attachment] = {}
    for a in items:
        if a.year != rok or a.kind not in REPORTS:
            continue
        if a.kind in out and out[a.kind].url != a.url:
            raise DiscoveryError(f"{rok} {a.kind}: dwa pliki ({out[a.kind].title} / {a.title})")
        out[a.kind] = a
    brak = [k for k in REPORTS if k not in out]
    if brak:
        raise DiscoveryError(f"{rok}: brak plików po IV kwartałach: {brak}")
    return out
