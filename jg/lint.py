"""Language linter: forbidden words (no evaluative language in published text)."""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from pathlib import Path

# "tylko" ("only") is forbidden in its evaluative sense; the linter does not check it because
# the word also has a neutral sense ("tylko dane z rejestrów").
FORBIDDEN = (
    "ukrywa",
    "ukrywają",
    "zaniżył",
    "zaniżyła",
    "zaniżyło",
    "nie realizuje",
    "skandal",
    "skandaliczny",
    "skandaliczne",
    "niestety",
    "zaledwie",
    "aż",
    "rekordowy",
    "rekordowe",
    "rekordowa",
    "dramatyczny",
    "dramatyczne",
    "dramatyczna",
    "kontrowersyjny",
    "kontrowersyjne",
    "kontrowersyjna",
    "szokujący",
    "szokujące",
    "szokująca",
)
_PATTERN = re.compile(
    r"(?<![\w])(" + "|".join(re.escape(w) for w in FORBIDDEN) + r")(?![\w])", re.IGNORECASE
)
_TAGS = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    word: str
    context: str


def check_text(text: str, path: str = "<tekst>") -> list[Finding]:
    findings = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in _PATTERN.finditer(line):
            findings.append(Finding(path, number, match.group(1), line.strip()[:120]))
    return findings


def check_html(html: str, path: str = "<html>") -> list[Finding]:
    """Check the text visible on the page (without tags, scripts and styles)."""
    stripped = _TAGS.sub(lambda m: "\n" * m.group(0).count("\n"), html)
    text = html_lib.unescape(stripped).replace("\u00ad", "").replace("\u200b", "")
    return check_text(text, path)


def check_paths(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        text = path.read_bytes().decode("utf-8", errors="replace")
        checker = check_html if path.suffix in (".html", ".htm") else check_text
        findings.extend(checker(text, str(path)))
    return findings
