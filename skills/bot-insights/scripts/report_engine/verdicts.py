"""Per-host triage verdict logic.

Shared by ``scorecard_entity_review`` (single host) and ``scorecard_brief``
(fleet of hosts). The fleet view classifies each host with the same
4-state taxonomy and aggregates counts into a triage strip; the entity
view renders a single verdict pill.

States:
- ``assign`` — needs operator attention now (escalate-severity band, or
  monitor-severity band with at least one triggered rule).
- ``watch`` — triggered something but the score still places it in the
  observe band; track but don't necessarily page.
- ``close_as_expected`` — no rules triggered, coverage is sufficient to
  trust the result.
- ``insufficient_data`` — fires only when no rules triggered AND half or
  more rule inputs are missing. A host that triggered a rule keeps its
  trigger-derived verdict (Assign / Watch) — the trigger is the evidence,
  and the coverage gap surfaces via the Low-confidence chip rather than
  by demoting the host to Insufficient data. This keeps the queue
  legible: the verdict pill answers "is there something to do here?"
  while the chip answers "how thin is the data behind that answer?".
"""

from __future__ import annotations


# Band names emitted by scorecard.score_band(): urgent_review, high_review,
# medium_review, low_review, observe. Group them into severity tiers so the
# verdict logic doesn't depend on the exact threshold splits.
ESCALATE_BANDS = frozenset({"urgent_review", "high_review"})
MONITOR_BANDS = frozenset({"medium_review", "low_review"})
OBSERVE_BANDS = frozenset({"observe"})

# Fraction of rules that must be unscored before coverage is "thin".
INSUFFICIENT_DATA_THRESHOLD = 0.5
LOW_CONFIDENCE_THRESHOLD = 0.25


def missing_input_ratio(rule_counts: dict) -> float:
    total = rule_counts.get("total") or 0
    if not total:
        return 0.0
    return (rule_counts.get("missing_input") or 0) / total


def classify(band: str, rule_counts: dict) -> dict:
    """Return the triage verdict for a single host.

    Returns ``{state, label, tone, rationale}``. ``tone`` is one of
    ``escalate`` / ``monitor`` / ``observe`` / ``neutral`` for pill
    styling.
    """
    triggered = rule_counts.get("triggered") or 0
    missing = rule_counts.get("missing_input") or 0
    total = rule_counts.get("total") or 0
    ratio = missing_input_ratio(rule_counts)

    # ``insufficient_data`` only overrides the band-derived state when the
    # host has nothing actionable: no rules triggered AND most rule inputs
    # missing. A host that *did* trigger something is still actionable —
    # the trigger is the evidence — so it keeps its triggered-state verdict
    # and surfaces the coverage gap via the confidence chip instead.
    if total and ratio >= INSUFFICIENT_DATA_THRESHOLD and triggered == 0:
        return {
            "state": "insufficient_data",
            "label": "Insufficient data",
            "tone": "neutral",
            "rationale": (
                f"Impact cannot be judged — {missing} of {total} "
                "rule inputs are missing and no rules triggered."
            ),
        }
    if band in ESCALATE_BANDS:
        return {
            "state": "assign",
            "label": "Assign",
            "tone": "escalate",
            "rationale": (
                f"Score landed in escalate-severity band with {triggered} "
                f"rule{'s' if triggered != 1 else ''} triggered."
            ),
        }
    if band in MONITOR_BANDS and triggered >= 1:
        return {
            "state": "assign",
            "label": "Assign",
            "tone": "monitor",
            "rationale": (
                f"{triggered} rule{'s' if triggered != 1 else ''} triggered "
                "in monitor-severity band — investigate before deciding."
            ),
        }
    if band in OBSERVE_BANDS and triggered >= 1:
        return {
            "state": "watch",
            "label": "Watch",
            "tone": "observe",
            "rationale": (
                f"{triggered} rule{'s' if triggered != 1 else ''} triggered "
                "but score remains in observe band."
            ),
        }
    if triggered == 0:
        return {
            "state": "close_as_expected",
            "label": "Close — expected",
            "tone": "observe",
            "rationale": "No rules triggered and rule coverage is sufficient.",
        }
    return {
        "state": "watch",
        "label": "Watch",
        "tone": "monitor",
        "rationale": (f"{triggered} rule{'s' if triggered != 1 else ''} triggered."),
    }


def confidence_chip(rule_counts: dict) -> dict | None:
    """Return a 'Low confidence: X of Y rules missing inputs' chip when
    coverage is thin (≥ 25% missing). Otherwise ``None``.
    """
    total = rule_counts.get("total") or 0
    missing = rule_counts.get("missing_input") or 0
    if not total or missing_input_ratio(rule_counts) < LOW_CONFIDENCE_THRESHOLD:
        return None
    return {
        "label": f"Low confidence: {missing} of {total} rules missing inputs",
        "tone": "neutral",
        "missing_count": missing,
        "total_count": total,
    }


# State ordering for queue sorts and triage-strip rendering — Assign first
# (most urgent), Insufficient data last (still needs operator attention but
# different action: fix the input, not the host).
STATE_ORDER = (
    "assign",
    "watch",
    "insufficient_data",
    "close_as_expected",
)

STATE_LABELS = {
    "assign": "Assign",
    "watch": "Watch",
    "insufficient_data": "Insufficient data",
    "close_as_expected": "Close — expected",
}

# Tone preferred per state for triage-strip pills. ``assign`` uses escalate
# tone regardless of what classify() returned for an individual host so the
# count strip reads as severity-coded.
STATE_TONE = {
    "assign": "escalate",
    "watch": "monitor",
    "insufficient_data": "neutral",
    "close_as_expected": "observe",
}
