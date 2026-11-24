"""SVG charts generated at build time.

- Dot map: points of all gminy (PRG), one shared file for the whole site; the gmina position is
  overlaid on the page in percent (`map_position`).
- Capital expenditure plan/execution bars: axis from zero, plan `accent-soft`, execution `accent`,
  missing data as a hatched area (never a zero bar).
- Comparison group dot strip: one shared file per group, metric and year; the gmina dot is
  overlaid in percent (`strip_position`). Axis order from lowest to highest.
Colours come only from the tokens (`site/design-tokens.json`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from html import escape

LAT0, LAT1 = 49.0, 54.9
LON0, LON1 = 14.0, 24.2
KX = math.cos(math.radians(52.0))
MAP_W = 600.0
MAP_H = round(MAP_W * (LAT1 - LAT0) / ((LON1 - LON0) * KX), 1)


def project(lat: float, lon: float) -> tuple[float, float]:
    x = (lon - LON0) / (LON1 - LON0) * MAP_W
    y = (LAT1 - lat) / (LAT1 - LAT0) * MAP_H
    return round(x, 1), round(y, 1)


def map_position(lat: float, lon: float) -> tuple[float, float]:
    """Point position on the map as a percentage of width and height."""
    x, y = project(lat, lon)
    return round(x / MAP_W * 100, 2), round(y / MAP_H * 100, 2)


def dot_map(points: list[tuple[float, float]], color: str, r: float = 1.6) -> str:
    dots = "".join(f'<circle cx="{x}" cy="{y}" r="{r}"/>' for x, y in (project(*p) for p in points))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {MAP_W:g} {MAP_H:g}" '
        f'role="img" aria-label="Mapa Polski: punkty {len(points)} gmin">'
        f'<g fill="{color}">{dots}</g></svg>'
    )


@dataclass(frozen=True)
class Slupek:
    rok: int
    plan: Decimal | None
    wykonanie: Decimal | None


def bars(data: list[Slupek], colors: dict[str, str], title: str) -> str:
    """Plan/execution bars in million PLN; missing data hatched. Value axis from zero."""
    w, h, pad_l, pad_b, pad_t = 560, 220, 44, 28, 12
    vals = [float(v) / 1e6 for s in data for v in (s.plan, s.wykonanie) if v is not None]
    top = _nice(max(vals) if vals else 1)
    inner_w, inner_h = w - pad_l - 8, h - pad_b - pad_t
    slot = inner_w / max(len(data), 1)
    bw = min(22, slot / 3)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" '
        f'aria-label="{escape(title)}" class="chart">',
        '<defs><pattern id="na" width="6" height="6" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)">'
        f'<rect width="6" height="6" fill="{colors["na-stripe-a"]}"/>'
        f'<rect width="3" height="6" fill="{colors["na-stripe-b"]}"/></pattern></defs>',
    ]
    for i in range(5):
        v = top * i / 4
        y = pad_t + inner_h - inner_h * i / 4
        parts.append(
            f'<line x1="{pad_l}" x2="{w - 8}" y1="{y:.1f}" y2="{y:.1f}" '
            f'stroke="{colors["line"]}"/>'
            f'<text x="{pad_l - 6}" y="{y + 4:.1f}" text-anchor="end" font-size="11" '
            f'fill="{colors["ink-2"]}">{_fmt(v)}</text>'
        )
    for i, s in enumerate(data):
        x0 = pad_l + slot * i + (slot - 2 * bw - 4) / 2
        for j, (v, fill) in enumerate(
            ((s.plan, colors["accent-soft"]), (s.wykonanie, colors["accent"]))
        ):
            x = x0 + j * (bw + 4)
            if v is None:
                parts.append(
                    f'<rect x="{x:.1f}" y="{pad_t}" width="{bw:.1f}" '
                    f'height="{inner_h}" fill="url(#na)"/>'
                )
                continue
            bh = inner_h * float(v) / 1e6 / top if top else 0
            parts.append(
                f'<rect x="{x:.1f}" y="{pad_t + inner_h - bh:.1f}" width="{bw:.1f}" '
                f'height="{bh:.1f}" fill="{fill}"/>'
            )
        parts.append(
            f'<text x="{pad_l + slot * i + slot / 2:.1f}" y="{h - 8}" '
            f'text-anchor="middle" font-size="12" fill="{colors["ink"]}">{s.rok}</text>'
        )
    parts.append(
        f'<line x1="{pad_l}" x2="{w - 8}" y1="{pad_t + inner_h}" '
        f'y2="{pad_t + inner_h}" stroke="{colors["ink"]}"/></svg>'
    )
    return "".join(parts)


def _nice(v: float) -> float:
    if v <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * exp:
            return m * exp
    return 10 * exp


def _fmt(v: float) -> str:
    text = f"{v:.1f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


STRIP_W, STRIP_H, STRIP_PAD = 600, 40, 10


def strip_position(value: Decimal, lo: Decimal, hi: Decimal) -> float:
    """Value position on the group strip as a percentage of width."""
    if hi == lo:
        return 50.0
    x = STRIP_PAD + (STRIP_W - 2 * STRIP_PAD) * float((value - lo) / (hi - lo))
    return round(x / STRIP_W * 100, 2)


def dot_strip(values: list[Decimal], color: str, axis: str, label: str) -> str:
    """Group dot strip (values from lowest to highest)."""
    lo, hi = min(values), max(values)
    dots = "".join(
        f'<circle cx="{strip_position(v, lo, hi) / 100 * STRIP_W:.1f}" cy="{STRIP_H / 2}" r="3"/>'
        for v in sorted(values)
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {STRIP_W} {STRIP_H}" '
        f'role="img" aria-label="{escape(label)}">'
        f'<line x1="{STRIP_PAD}" x2="{STRIP_W - STRIP_PAD}" y1="{STRIP_H / 2}" '
        f'y2="{STRIP_H / 2}" stroke="{axis}"/>'
        f'<g fill="{color}" fill-opacity="0.45">{dots}</g></svg>'
    )
