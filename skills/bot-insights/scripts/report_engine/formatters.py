"""String/number/date formatters exposed to Jinja templates."""

from __future__ import annotations

import re
from datetime import datetime


def window_fmt(window: dict) -> str:
    """Format a {start,end} ISO window as 'YYYY-MM-DD HH:MM → ... UTC'."""
    start = datetime.fromisoformat(window["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(window["end"].replace("Z", "+00:00"))
    return f"{start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} UTC"


def big_number(value: float | int) -> str:
    """Compact human-readable number: 11.81B, 662.43M, 23.33K, 999."""
    n = float(value)
    sign = "-" if n < 0 else ""
    n = abs(n)
    for divisor, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= divisor:
            return f"{sign}{n / divisor:.2f}{suffix}"
    if n >= 1:
        return f"{sign}{n:.0f}"
    return f"{sign}{n:.2f}"


def signed_pct(value: float, digits: int = 1) -> str:
    """Format a percentage with explicit sign: +12.3%, -0.4%, ±0.0%."""
    if abs(value) < 10**-digits / 2:
        return f"±0.{'0' * digits}%"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.{digits}f}%"


def signed_pp(value: float, digits: int = 1) -> str:
    """Same as signed_pct but reads as percentage points (no % suffix)."""
    if abs(value) < 10**-digits / 2:
        return f"±0.{'0' * digits}pp"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.{digits}f}pp"


def format_share_pct(pct: float) -> str:
    """Format a fleet-share percent — drop trailing zero, preserve precision
    near 100% so a 99.97% reading doesn't round to a clean 100%."""
    if pct >= 99 and pct < 100:
        return f"{pct:.2f}%"
    if abs(pct - round(pct)) < 0.05:
        return f"{int(round(pct))}%"
    return f"{pct:.1f}%"


def pct2(value: float) -> str:
    """Format a number as a 2-decimal percent: 5 → '5.00%', 0.5 → '0.50%'.

    Reader-facing convention across this engine: any percentage uses two
    decimals.
    """
    return f"{value:.2f}%"


_PERCENT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")


def normalize_percents(text: str) -> str:
    """Reformat any percentage embedded in a string to 2 decimals.

    Producer-supplied evidence sentences may carry full instrument precision
    (e.g. ``"Cache miss rate is 99.992029%."``). This filter normalizes
    embedded percentages to ``"99.99%"`` without disturbing surrounding
    prose. Safe to apply to arbitrary strings; no-op when no percentage
    pattern is present.
    """
    if not text:
        return text

    def _fix(match: re.Match[str]) -> str:
        return f"{float(match.group(1)):.2f}%"

    return _PERCENT_RE.sub(_fix, text)
