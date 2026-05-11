"""Chart helpers that emit inline SVG strings.

Kept in Python (not in templates) because the math is awkward in Jinja and
because they're worth unit-testing independently. Exposed to templates as
globals via `render.py`.
"""

from __future__ import annotations

import math

from .theme import BAND_COLORS, PALETTE, band_for_score


def score_gauge_svg(score: int, delta_pct: float | None = None) -> str:
    """Half-circle arc gauge with band-zoned arc, big-number readout, and an
    optional delta indicator (`↑ 5.00%` / `↓ 1.00%` / `— 0.00%`) directly
    under the number. The delta is the percent change of THIS host's score
    versus its prior equivalent window."""
    width, height = 280, 195
    cx, cy, r = 140, 130, 95
    stroke = 18

    def arc_path(v1: float, v2: float) -> str:
        th1 = math.pi * (1 - v1 / 100)
        th2 = math.pi * (1 - v2 / 100)
        x1, y1 = cx + r * math.cos(th1), cy - r * math.sin(th1)
        x2, y2 = cx + r * math.cos(th2), cy - r * math.sin(th2)
        return f"M {x1:.2f} {y1:.2f} A {r} {r} 0 0 1 {x2:.2f} {y2:.2f}"

    th_p = math.pi * (1 - max(0, min(100, score)) / 100)
    px = cx + r * math.cos(th_p)
    py = cy - r * math.sin(th_p)
    pxi = cx + (r - stroke - 6) * math.cos(th_p)
    pyi = cy - (r - stroke - 6) * math.sin(th_p)

    band_label, score_color = band_for_score(score)

    # Delta: rendered just under the big number. Higher score = healthier,
    # so a positive percent change is an improvement (green ↑); a negative
    # change is a degradation (red ↓); near-zero shows an em-dash + 0.00%.
    delta_text = ""
    if delta_pct is not None:
        if delta_pct > 0.005:
            delta_color = PALETTE["delta_down"]
            delta_text = f"↑ {delta_pct:.2f}%"
        elif delta_pct < -0.005:
            delta_color = PALETTE["escalate"]
            delta_text = f"↓ {abs(delta_pct):.2f}%"
        else:
            delta_color = PALETTE["muted"]
            delta_text = "Unchanged"

    delta_svg = ""
    if delta_text:
        delta_svg = (
            f'<text x="{cx}" y="158" text-anchor="middle" '
            f'class="gauge-delta" fill="{delta_color}">{delta_text}</text>'
        )

    return (
        f'<svg viewBox="0 0 {width} {height}" class="gauge-svg" '
        f'role="img" aria-label="Score {score}: {band_label}">'
        f'<path d="{arc_path(0, 40)}" stroke="{PALETTE["escalate_fill"]}" '
        f'stroke-width="{stroke}" fill="none" />'
        f'<path d="{arc_path(40, 70)}" stroke="{PALETTE["monitor_fill"]}" '
        f'stroke-width="{stroke}" fill="none" />'
        f'<path d="{arc_path(70, 100)}" stroke="{PALETTE["observe_fill"]}" '
        f'stroke-width="{stroke}" fill="none" />'
        f'<line x1="{pxi:.2f}" y1="{pyi:.2f}" x2="{px:.2f}" y2="{py:.2f}" '
        f'stroke="{score_color}" stroke-width="3" stroke-linecap="round" />'
        f'<circle cx="{px:.2f}" cy="{py:.2f}" r="6" '
        f'fill="{score_color}" stroke="#fff" stroke-width="2" />'
        f'<text x="{cx - r + 4}" y="{cy + 16}" text-anchor="start" '
        f'class="gauge-tick">0</text>'
        f'<text x="{cx + r - 4}" y="{cy + 16}" text-anchor="end" '
        f'class="gauge-tick">100</text>'
        f'<text x="{cx}" y="{cy - 18}" text-anchor="middle" '
        f'class="gauge-number">{score}</text>'
        f"{delta_svg}"
        f'<text x="{cx}" y="180" text-anchor="middle" '
        f'class="gauge-band" fill="{score_color}">{band_label}</text>'
        "</svg>"
    )


def score_bar_svg(score: int, max_score: int = 100) -> str:
    """Compact horizontal score bar for use inside table rows."""
    width, height = 88, 8
    pct = max(0, min(score, max_score)) / max_score
    fill_w = pct * width
    _, color = band_for_score(score)
    return (
        f'<svg viewBox="0 0 {width} {height}" class="score-bar" '
        f'role="img" aria-label="Score {score}">'
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="2" fill="#f4f4f5" />'
        f'<rect x="0" y="0" width="{fill_w:.1f}" height="{height}" rx="2" fill="{color}" />'
        "</svg>"
    )


def band_distribution_bar_svg(
    bands: dict[str, int], width: int = 280, height: int = 44
) -> str:
    """Horizontal stacked bar showing fleet distribution across health bands.

    Order is escalate (worst, left) → monitor → observe (best, right) so the
    visual gradient reads "concern" on the left, "healthy" on the right —
    matching the gauge's arc convention. Below-bar labels carry the counts.
    """
    ordered = (
        ("escalate", bands.get("escalate", 0), PALETTE["escalate"]),
        ("monitor", bands.get("monitor", 0), PALETTE["monitor"]),
        ("observe", bands.get("observe", 0), PALETTE["observe"]),
    )
    total = sum(count for _, count, _ in ordered)
    if total == 0:
        return ""

    bar_h = 16
    bar_y = 2
    label_y = bar_h + bar_y + 14
    parts = [
        f'<svg viewBox="0 0 {width} {height}" class="band-dist-bar" '
        f'role="img" aria-label="Band distribution">'
    ]

    x = 0.0
    label_xs: list[tuple[str, int, float]] = []
    for name, count, color in ordered:
        if count == 0:
            continue
        w = (count / total) * width
        parts.append(
            f'<rect x="{x:.1f}" y="{bar_y}" width="{w:.1f}" '
            f'height="{bar_h}" fill="{color}" rx="2" />'
        )
        label_xs.append((name, count, x + w / 2))
        x += w

    # Labels below: positioned at each segment's center if there's room,
    # else collapsed to a single centered label.
    if len(label_xs) == 1:
        name, count, _ = label_xs[0]
        parts.append(
            f'<text x="{width / 2:.1f}" y="{label_y}" text-anchor="middle" '
            f'class="band-dist-label">{count} {name.capitalize()}</text>'
        )
    else:
        for name, count, cx in label_xs:
            parts.append(
                f'<text x="{cx:.1f}" y="{label_y}" text-anchor="middle" '
                f'class="band-dist-label">{count} {name.capitalize()}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def score_histogram_svg(
    scores: list[int],
    lowest: int,
    median: int,
    bin_size: int = 5,
    width: int = 660,
    height: int = 200,
) -> str:
    """Score-distribution histogram with band-zoned background.

    The chart simultaneously communicates *where* the fleet sits (the bars)
    and *what that means* in severity terms (the colored zones behind them).
    Annotations: lowest score and median plotted on the x-axis as ticks. When
    lowest and median coincide they collapse into a single combined label.
    """
    if not scores:
        return ""
    pad_l, pad_r, pad_t, pad_b = 36, 20, 26, 44
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    # Bucket scores. Score=100 collapses into the 90 bucket so the chart
    # caps at the visible 0..100 range.
    buckets: dict[int, int] = {}
    for s in scores:
        bin_key = (min(s, 99) // bin_size) * bin_size
        buckets[bin_key] = buckets.get(bin_key, 0) + 1
    max_count = max(buckets.values())

    def x_for_score(s: float) -> float:
        return pad_l + (s / 100) * plot_w

    def color_for_score(s: int) -> str:
        if s < 40:
            return PALETTE["escalate"]
        if s < 70:
            return PALETTE["monitor"]
        return PALETTE["observe"]

    parts = [
        f'<svg viewBox="0 0 {width} {height}" class="score-hist" '
        f'role="img" aria-label="Score distribution">'
    ]

    # Band zones (background)
    zones = (
        ("Escalate", 0, 40, PALETTE["escalate_fill"]),
        ("Monitor", 40, 70, PALETTE["monitor_fill"]),
        ("Observe", 70, 100, PALETTE["observe_fill"]),
    )
    for label, lo, hi, fill in zones:
        zx = x_for_score(lo)
        zw = x_for_score(hi) - zx
        parts.append(
            f'<rect x="{zx:.1f}" y="{pad_t}" width="{zw:.1f}" '
            f'height="{plot_h}" fill="{fill}" fill-opacity="0.40" />'
        )
        center_x = zx + zw / 2
        parts.append(
            f'<text x="{center_x:.1f}" y="{pad_t - 8}" '
            f'text-anchor="middle" class="hist-zone-label">{label}</text>'
        )

    # Bars (one per non-empty bucket)
    bar_top_pad = 10
    for bin_key, count in sorted(buckets.items()):
        bx = x_for_score(bin_key)
        bx_end = x_for_score(bin_key + bin_size)
        bar_w = max(1.0, bx_end - bx - 2)
        bar_h = (count / max_count) * (plot_h - bar_top_pad)
        bar_y = pad_t + plot_h - bar_h
        color = color_for_score(bin_key)
        parts.append(
            f'<rect x="{bx + 1:.1f}" y="{bar_y:.1f}" '
            f'width="{bar_w:.1f}" height="{bar_h:.1f}" '
            f'fill="{color}" rx="2" />'
        )
        parts.append(
            f'<text x="{(bx + bx_end) / 2:.1f}" y="{bar_y - 4:.1f}" '
            f'text-anchor="middle" class="hist-bar-count">{count}</text>'
        )

    # X-axis line + ticks
    axis_y = pad_t + plot_h
    parts.append(
        f'<line x1="{pad_l}" y1="{axis_y}" x2="{width - pad_r}" y2="{axis_y}" '
        f'stroke="#d4d4d8" stroke-width="1" />'
    )
    for tick in (0, 25, 50, 75, 100):
        tx = x_for_score(tick)
        parts.append(
            f'<line x1="{tx:.1f}" y1="{axis_y}" x2="{tx:.1f}" y2="{axis_y + 4}" '
            f'stroke="#a1a1aa" stroke-width="1" />'
        )
        parts.append(
            f'<text x="{tx:.1f}" y="{axis_y + 18}" text-anchor="middle" '
            f'class="hist-axis-tick">{tick}</text>'
        )

    # Annotations: lowest + median
    annot_y = axis_y + 36
    if lowest == median:
        ax = x_for_score(lowest)
        parts.append(
            f'<line x1="{ax:.1f}" y1="{axis_y - 6}" x2="{ax:.1f}" '
            f'y2="{axis_y + 6}" stroke="#18181b" stroke-width="2" />'
        )
        parts.append(
            f'<text x="{ax:.1f}" y="{annot_y}" text-anchor="middle" '
            f'class="hist-annotation">Lowest = Median = {lowest}</text>'
        )
    else:
        for label, value, color, dash in (
            ("Lowest", lowest, "#18181b", ""),
            ("Median", median, "#71717a", 'stroke-dasharray="3,2"'),
        ):
            ax = x_for_score(value)
            parts.append(
                f'<line x1="{ax:.1f}" y1="{axis_y - 6}" x2="{ax:.1f}" '
                f'y2="{axis_y + 6}" stroke="{color}" stroke-width="2" {dash} />'
            )
            parts.append(
                f'<text x="{ax:.1f}" y="{annot_y}" text-anchor="middle" '
                f'class="hist-annotation">{label} {value}</text>'
            )

    parts.append("</svg>")
    return "".join(parts)


def triage_histogram_svg(
    counts: dict[str, int],
    width: int = 660,
    height: int = 200,
) -> str:
    """4-bar histogram bucketed by triage verdict state.

    Replaces the score-distribution histogram in the brief landscape — the
    question shifts from "where do scores sit on the 0–100 scale" (which
    reads as reassuring when most hosts are in the blue zone even if they
    triggered something) to "where does the work sit in the queue."
    Bar order: Assign → Watch → Insufficient → Close. Bar color matches
    the triage pill tone for visual continuity with the strip.
    """
    order = (
        ("assign", "Assign", PALETTE["escalate"]),
        ("watch", "Watch", PALETTE["monitor"]),
        ("insufficient_data", "Insufficient", PALETTE["muted"]),
        ("close_as_expected", "Close — expected", PALETTE["observe"]),
    )
    values = [(label, counts.get(state, 0), color) for state, label, color in order]
    total = sum(v for _, v, _ in values)
    if total == 0:
        return ""

    pad_l, pad_r, pad_t, pad_b = 36, 20, 18, 44
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    max_count = max((v for _, v, _ in values), default=1) or 1
    n = len(values)
    gap = 18
    bar_w = (plot_w - gap * (n - 1)) / n

    parts = [
        f'<svg viewBox="0 0 {width} {height}" class="triage-hist" '
        f'role="img" aria-label="Triage state distribution">'
    ]

    axis_y = pad_t + plot_h
    parts.append(
        f'<line x1="{pad_l}" y1="{axis_y}" x2="{width - pad_r}" y2="{axis_y}" '
        f'stroke="#d4d4d8" stroke-width="1" />'
    )

    bar_top_pad = 14
    for i, (label, count, color) in enumerate(values):
        bx = pad_l + i * (bar_w + gap)
        bar_height = (count / max_count) * (plot_h - bar_top_pad)
        by = axis_y - bar_height
        opacity = "0.35" if count == 0 else "1"
        parts.append(
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" '
            f'height="{bar_height:.1f}" fill="{color}" fill-opacity="{opacity}" '
            f'rx="3" />'
        )
        parts.append(
            f'<text x="{bx + bar_w / 2:.1f}" y="{by - 6:.1f}" '
            f'text-anchor="middle" class="hist-bar-count">{count}</text>'
        )
        parts.append(
            f'<text x="{bx + bar_w / 2:.1f}" y="{axis_y + 18:.1f}" '
            f'text-anchor="middle" class="hist-axis-tick">{label}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def coverage_bar_svg(triggered: int, evaluated_zero: int, missing: int) -> str:
    """Stacked horizontal bar: triggered / evaluated_zero / missing_input."""
    total = triggered + evaluated_zero + missing
    if total == 0:
        return ""
    width, height = 360, 14
    parts = [
        f'<svg viewBox="0 0 {width} {height}" class="coverage-bar" '
        f'role="img" aria-label="Rule coverage">'
    ]
    x = 0.0
    segments = (
        (triggered, BAND_COLORS["observe"]),
        (evaluated_zero, PALETTE["coverage_evaluated_zero"]),
        (missing, PALETTE["coverage_missing"]),
    )
    for count, color in segments:
        if count == 0:
            continue
        w = (count / total) * width
        parts.append(
            f'<rect x="{x:.1f}" y="0" width="{w:.1f}" height="{height}" '
            f'fill="{color}" />'
        )
        x += w
    parts.append("</svg>")
    return "".join(parts)


def bullet_chart_svg(
    actual: float,
    comparison: float,
    ranges: list[tuple[float, str]] | None = None,
    label: str = "",
    width: int = 360,
    height: int = 36,
) -> str:
    """Stephen Few bullet chart — actual bar over qualitative band background,
    with a vertical tick marking the comparison value.

    ranges: list of (upper_bound, fill_color) sorted ascending. If omitted,
            uses the score-band defaults (escalate 0-40, monitor 40-70,
            observe 70-100).
    """
    if ranges is None:
        ranges = [
            (40, PALETTE["escalate_fill"]),
            (70, PALETTE["monitor_fill"]),
            (100, PALETTE["observe_fill"]),
        ]

    band_h = height - 14  # leave room for label below
    parts = [
        f'<svg viewBox="0 0 {width} {height}" class="bullet-chart" '
        f'role="img" aria-label="{label or "bullet chart"}">'
    ]
    prev_x = 0.0
    for upper, color in ranges:
        x_end = (upper / 100) * width
        parts.append(
            f'<rect x="{prev_x:.1f}" y="0" '
            f'width="{(x_end - prev_x):.1f}" height="{band_h}" fill="{color}" />'
        )
        prev_x = x_end

    actual_w = max(0, min(actual, 100)) / 100 * width
    actual_color = band_for_score(int(actual))[1]
    actual_h = max(8, band_h - 14)
    actual_y = (band_h - actual_h) / 2
    parts.append(
        f'<rect x="0" y="{actual_y:.1f}" '
        f'width="{actual_w:.1f}" height="{actual_h:.1f}" fill="{actual_color}" />'
    )

    comp_x = max(0, min(comparison, 100)) / 100 * width
    parts.append(
        f'<line x1="{comp_x:.1f}" y1="2" x2="{comp_x:.1f}" '
        f'y2="{band_h - 2:.1f}" stroke="#18181b" stroke-width="2.5" />'
    )

    if label:
        parts.append(
            f'<text x="0" y="{height - 2}" class="bullet-label">{label}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def slopegraph_svg(
    entities: list[dict],
    label_left: str = "baseline",
    label_right: str = "current",
    width: int = 540,
    height: int = 280,
) -> str:
    """Two-column slopegraph for scoreable entities with a delta.

    Each entity is a dict with `entity`, `score`, `delta`. We plot
    (score - delta) on the left, score on the right.
    """
    if not entities:
        return ""

    pairs = [(e["entity"], e["score"] - e["delta"], e["score"]) for e in entities]
    pad_l, pad_r, pad_t, pad_b = 80, 200, 36, 30
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    all_values = [v for _, b, a in pairs for v in (b, a)]
    vmin, vmax = min(all_values), max(all_values)
    span = max(vmax - vmin, 1.0)

    def y_for(v: float) -> float:
        return pad_t + (1 - (v - vmin) / span) * plot_h

    x_left = pad_l
    x_right = pad_l + plot_w

    parts = [
        f'<svg viewBox="0 0 {width} {height}" class="slopegraph" '
        f'role="img" aria-label="Score movement">'
    ]
    # Column headers
    parts.append(
        f'<text x="{x_left}" y="{pad_t - 14}" text-anchor="middle" '
        f'class="slope-axis-label">{label_left}</text>'
    )
    parts.append(
        f'<text x="{x_right}" y="{pad_t - 14}" text-anchor="middle" '
        f'class="slope-axis-label">{label_right}</text>'
    )

    for entity, before, after in pairs:
        y_b = y_for(before)
        y_a = y_for(after)
        if after < before:
            color = PALETTE["escalate"]
        elif after > before:
            color = PALETTE["delta_down"]  # green tone (improvement)
        else:
            color = PALETTE["muted_2"]
        parts.append(
            f'<line x1="{x_left}" y1="{y_b:.1f}" x2="{x_right}" y2="{y_a:.1f}" '
            f'stroke="{color}" stroke-width="1.5" stroke-opacity="0.65" />'
        )
        parts.append(f'<circle cx="{x_left}" cy="{y_b:.1f}" r="4" fill="{color}" />')
        parts.append(f'<circle cx="{x_right}" cy="{y_a:.1f}" r="4" fill="{color}" />')
        parts.append(
            f'<text x="{x_right + 8}" y="{y_a + 4:.1f}" class="slope-entity-label">'
            f"{entity}: {before:.0f} → {after:.0f}</text>"
        )

    parts.append("</svg>")
    return "".join(parts)


def sparkline_svg(
    values: list[float],
    width: int = 120,
    height: int = 32,
    color: str = PALETTE["observe"],
) -> str:
    """Single-series sparkline. Empty values returns an empty string."""
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return ""
    vmin, vmax = min(values), max(values)
    span = vmax - vmin or 1.0
    pad = 2
    plot_w, plot_h = width - 2 * pad, height - 2 * pad
    pts = []
    for i, v in enumerate(values):
        x = pad + (i / (len(values) - 1)) * plot_w
        y = pad + (1 - (v - vmin) / span) * plot_h
        pts.append(f"{x:.1f},{y:.1f}")
    return (
        f'<svg viewBox="0 0 {width} {height}" class="sparkline" '
        f'role="img" aria-label="Trend">'
        f'<polyline fill="none" stroke="{color}" stroke-width="1.5" '
        f'points="{" ".join(pts)}" />'
        "</svg>"
    )
