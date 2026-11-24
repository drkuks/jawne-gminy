"""Number and date formatting, one helper for data and the website (based on okiemnaolawe
`oko/site/format.py`). Matches `Intl.NumberFormat('pl-PL')`: thousands grouped with a
non-breaking space from five digits (4999, 20 000), decimal comma, minus U+2212;
missing value = „brak danych”. Also used as Jinja2 filters."""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

NBSP = " "
MINUS = "−"
MISSING = "brak danych"


def _decimal(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _group(integer: str) -> str:
    sign = MINUS if integer.startswith("-") else ""
    digits = integer.lstrip("-")
    if len(digits) <= 4:  # pl-PL: 4-digit numbers without a separator
        return sign + digits
    parts = []
    while len(digits) > 3:
        parts.insert(0, digits[-3:])
        digits = digits[:-3]
    parts.insert(0, digits)
    return sign + NBSP.join(parts)


def number(value, decimals: int = 0) -> str:
    """`1 891 014` or `1 891 014,21`; `None` → „brak danych”."""
    if value is None:
        return MISSING
    text = str(_decimal(value).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP))
    whole, _, frac = text.partition(".")
    if whole in ("-0", "0") and text.startswith("-") and not any(c not in "0." for c in frac):
        whole = "0"
    return _group(whole) + (f",{frac}" if decimals else "")


def pln(value) -> str:
    """Exact amount: `1 891 014,21 zł`."""
    return MISSING if value is None else f"{number(value, 2)}{NBSP}zł"


def rounded(value, unit: str = "zł") -> str:
    """Rounded amount: `1,89 mln zł`, `412,5 tys. zł`, `2,10 mld zł`."""
    if value is None:
        return MISSING
    amount = _decimal(value)
    magnitude = abs(amount)
    if magnitude >= Decimal("1e9"):
        return f"{number(amount / Decimal('1e9'), 2)}{NBSP}mld{NBSP}{unit}"
    if magnitude >= Decimal("1e6"):
        return f"{number(amount / Decimal('1e6'), 2)}{NBSP}mln{NBSP}{unit}"
    if magnitude >= Decimal("1e3"):
        return f"{number(amount / Decimal('1e3'), 1)}{NBSP}tys.{NBSP}{unit}"
    return f"{number(amount, 2)}{NBSP}{unit}"


def per_capita(value) -> str:
    """Amount per resident: `7 583,52 zł`."""
    return MISSING if value is None else f"{number(value, 2)}{NBSP}zł"


def percent(value) -> str:
    """`27%`; below 1% one decimal place (`0,8%`)."""
    if value is None:
        return MISSING
    amount = _decimal(value)
    decimals = 1 if 0 < abs(amount) < 1 else 0
    return f"{number(amount, decimals)}%"


def day(value: date | datetime | str | None) -> str:
    """`26.03.2026`; ISO text is accepted."""
    if value is None or value == MISSING:
        return MISSING
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return f"{value.day:02d}.{value.month:02d}.{value.year}"


def plural(n: int, one: str, few: str, many: str) -> str:
    """Polish plural: 1 gmina, 2–4 gminy (except 12–14), 5 gmin."""
    n = abs(int(n))
    if n == 1:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


def value(v, unit: str) -> str:
    """Indicator value formatted by its unit from metrics.yaml."""
    if v is None:
        return MISSING
    if unit == "%":
        return percent(v)
    if unit == "zł/mieszk.":
        return per_capita(v)
    if unit == "zł":
        return rounded(v)
    return number(v)


FILTERS = {
    "number": number,
    "pln": pln,
    "rounded": rounded,
    "percent": percent,
    "day": day,
    "plural": plural,
    "per_capita": per_capita,
    "value": value,
}
