"""Visual palette and label constants for the report engine.

Single source of truth for colors, band thresholds, and human-readable domain
labels. `PALETTE` is exposed to Jinja as a global, so both Python helpers
(`charts.py`) and the stylesheet (`_styles.css`) read from it. Retune here.
"""

from __future__ import annotations

# Tableau 10 base hues for severity bands, plus derived tints/borders/text
# colors for pills and large fills. Neutral chrome (bg/text/border) keeps
# zinc grays since neutrals don't compete with the warm accents.
PALETTE = {
    # Band primaries
    "observe": "#4E79A7",
    "monitor": "#F28E2B",
    "escalate": "#E15759",

    # Large fill tints (used for the gauge arc zones)
    "observe_fill": "#C9D5E5",
    "monitor_fill": "#FAD9B6",
    "escalate_fill": "#F4C8C9",

    # Pill colors — pale bg, mid border, deep text
    "observe_pill_bg": "#EDF2F8",
    "observe_pill_border": "#B5C7DC",
    "observe_pill_text": "#2D4A6B",
    "monitor_pill_bg": "#FDF1E2",
    "monitor_pill_border": "#F8C58A",
    "monitor_pill_text": "#8C4A0A",
    "escalate_pill_bg": "#FBE7E7",
    "escalate_pill_border": "#F0A8A9",
    "escalate_pill_text": "#8B2728",

    # Chrome / neutrals
    "bg": "#fafaf9",
    "surface": "#ffffff",
    "surface_2": "#f4f4f5",
    "text": "#18181b",
    "muted": "#71717a",
    "muted_2": "#a1a1aa",
    "border": "#e4e4e7",

    # Coverage-bar segments (triggered = observe accent, missing = warm tint)
    "coverage_evaluated_zero": "#a1a1aa",
    "coverage_missing": "#F8C58A",

    # Improvement (down/green) for delta arrows in entity tables
    "delta_down": "#3F8C5A",
}

# Convenience: just the band-primary hues, by band name
BAND_COLORS = {
    "observe": PALETTE["observe"],
    "monitor": PALETTE["monitor"],
    "escalate": PALETTE["escalate"],
}

# Score thresholds for arc-zone coloring on the gauge.
BAND_THRESHOLDS = {
    "observe": 70,
    "monitor": 40,
    "escalate": 0,
}

DOMAIN_LABELS = {
    "cache_busting": "Cache busting",
    "crawler_governance": "Crawler governance",
    "movement": "Movement",
    "origin_impact": "Origin impact",
    "policy_collateral": "Policy collateral",
    "security_evidence": "Security evidence",
    "none": "No domain triggered",
}

DOMAIN_ORDER = [
    "cache_busting",
    "crawler_governance",
    "movement",
    "origin_impact",
    "policy_collateral",
    "security_evidence",
]


def band_for_score(score: int) -> tuple[str, str]:
    """Return (band, hex_color) for a score under the default thresholds."""
    if score >= BAND_THRESHOLDS["observe"]:
        return "observe", BAND_COLORS["observe"]
    if score >= BAND_THRESHOLDS["monitor"]:
        return "monitor", BAND_COLORS["monitor"]
    return "escalate", BAND_COLORS["escalate"]
