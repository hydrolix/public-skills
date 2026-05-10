"""Context preparer for ``bot_control_review.v1`` — the Control Review
before/after report.

The control_review report compares an ``after_window`` against an explicit
``before_window`` (or an expected baseline) for the population the control
targets. It is multi-artifact: ``bot_control_review.v1`` is required, and
``bot_posture_movement.v1`` / ``bot_mover_attribution.v1`` /
``bot_timeseries.v1`` may attach as companions if they pass
``companion_compatible``. Companion artifacts are dropped with a warning
when their window/scope metadata does not align with the control —
the legacy renderer's behavior, preserved here.

Companion selection lives in ``_shared.select_control_companions`` so
the engine path does not depend on ``render_report.py``. M1.1 already
moved the formatting helpers into ``humanize`` and the delta helpers
into ``deltas``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..findings import Finding
from ..humanize import (
    cluster_display,
    human_metric_name,
)
from ._shared import select_control_companions

SCHEMA = "bot_control_review.v1"
REPORT_TYPE = "control_review"
TEMPLATE = "reports/control_review.html"

# Wrapper analyst-note routing. Same slot names every report type uses;
# the executive_summary / operational_interpretation / finding_overrides
# triplet covers the three places the LLM may speak in this report.
NOTE_ID_TO_SLOT = {
    "llm-interpretation": "executive_summary",
    "llm-operational": "operational_interpretation",
    "llm-finding-overrides": "finding_overrides",
}

PURPOSE = {
    "report_class_fleet": "Control Review — before/after for an applied control",
    "report_class_single": "Control Review — before/after for an applied control",
    "measures": (
        "Compares the after-control window against an explicit before window "
        "(or external baseline) for the entities targeted by the control."
    ),
    "score_legend": (
        "Per-metric direction and effect size, plus collateral and "
        "displacement checks for adjacent populations."
    ),
    "cant_say": (
        "Cannot claim the control caused the movement without external "
        "change evidence. Concurrent changes can confound the result."
    ),
}


def assemble(artifacts: list[dict]) -> dict:
    """Reshape a ``bot_report_input.v1`` wrapper's artifact list into the
    dict shape :func:`prepare` consumes.

    A wrapper carries one ``bot_control_review.v1`` plus optional
    posture / mover / timeseries companions. Companion compatibility is
    enforced by :func:`select_control_companions`; rejected companions
    surface as warnings on the supplied ``warn`` callable (none here —
    the engine routes warnings via the report renderer's ``ctx``).
    """
    selection = select_control_companions(artifacts)
    return {
        "schema_version": SCHEMA,
        "control": selection["control"],
        "posture": selection["posture"],
        "mover": selection["mover"],
        "timeseries": selection["timeseries"],
    }


def prepare(artifact: dict) -> dict:
    """Build the template context for ``reports/control_review.html``.

    The context model mirrors the other report types: ``title``, ``kicker``,
    ``headline``, ``dek`` for the header; ``purpose``/``orientation`` for
    the disclosure strip; ``scope``/``windows``/``method``/``confidence``
    for provenance; and report-specific keys (``target``, ``effects``,
    ``collateral_checks``, ``displacement_checks``, ``expected_basis``)
    for the body.
    """
    control = artifact["control"]
    scope = control.get("scope") or {}
    before = control.get("before_window") or {}
    after = control.get("after_window") or {}
    expected_window = control.get("expected_window") or {}

    target = control.get("target") or {}
    target_descriptor = _target_descriptor(target)

    effects = [
        _effect_row(effect)
        for effect in (control.get("target_effects") or [])
        if isinstance(effect, dict)
    ]
    bar_rows = [row for row in (_bar_row(r) for r in effects) if row is not None]
    collateral_checks = _check_rows(control.get("collateral_checks") or [])
    displacement_checks = _check_rows(control.get("displacement_checks") or [])

    cluster_label = _cluster_label(scope)
    headline = _headline(target_descriptor, cluster_label, after)

    expected_basis = control.get("expected_basis")
    findings = _findings(effects, target_descriptor, expected_basis)

    interpretation_constraints = (
        control.get("interpretation_constraints") or []
    )
    confidence_reasons = sorted(
        {
            reason
            for effect in (control.get("target_effects") or [])
            if isinstance(effect, dict)
            for reason in (effect.get("confidence_reasons") or [])
        }
    )

    return {
        "title": "Control Review",
        "kicker": PURPOSE["report_class_single"],
        "headline": headline,
        "dek": _dek(effects, target_descriptor),
        "purpose": None,
        "orientation": {
            "measures": PURPOSE["measures"],
            "score_legend": PURPOSE["score_legend"],
            "cant_say": PURPOSE["cant_say"],
        },
        "scope": {
            "cluster": scope.get("cluster") or cluster_label,
            "database": scope.get("database") or "",
            "table_used": control.get("table_used") or "",
        },
        "windows": {
            "current": after,
            "baseline": before,
            "expected": expected_window,
        },
        "target": {
            "descriptor": target_descriptor,
            "raw": target,
        },
        "effects": effects,
        "control_bars": bar_rows,
        "collateral_checks": collateral_checks,
        "displacement_checks": displacement_checks,
        "expected_basis": expected_basis,
        "expected_basis_label": _expected_basis_label(expected_basis),
        "findings": findings,
        "method": {
            "schema_version": control.get("schema_version"),
            "comparison_type": control.get("comparison_type"),
            "producer_limit": None,
            "result_row_count": len(effects),
            "result_truncated": False,
            "interpretation_constraints": interpretation_constraints,
        },
        "confidence": {
            "reasons": confidence_reasons,
        },
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# ---------------------------------------------------------------------------
# Effect / check row projection
# ---------------------------------------------------------------------------


def _effect_row(effect: dict) -> dict:
    """Project a ``target_effects`` entry into the row shape the template
    consumes. Keeps every numeric value as the producer emitted it so
    ``|pct2`` / ``|signed_pp`` filters can format consistently.
    """
    metric = effect.get("metric")
    return {
        "metric": metric,
        "metric_label": human_metric_name(metric),
        "before": _maybe_float(effect.get("before")),
        "after": _maybe_float(effect.get("after")),
        "expected": _maybe_float(effect.get("expected")),
        "absolute_delta_vs_expected": _maybe_float(
            effect.get("absolute_delta_vs_expected")
        ),
        "pct_change_vs_expected": _maybe_float(effect.get("pct_change_vs_expected")),
        "status": effect.get("status"),
        "status_label": _status_label(effect.get("status")),
        "status_tone": _status_tone(effect.get("status")),
        "confidence": effect.get("confidence") or "",
        "direction": effect.get("direction"),
    }


def _bar_row(row: dict) -> dict | None:
    """Project an effect row into the input the control_bars macro
    consumes. Returns ``None`` if every numeric value is missing so the
    macro can skip the row outright (matches legacy ``html_control_bars``
    behavior at ``render_report.py:3461``).
    """
    values = (row["before"], row["after"], row["expected"])
    if all(v is None for v in values):
        return None
    return {
        "metric": row["metric"],
        "metric_label": row["metric_label"],
        "before": row["before"],
        "after": row["after"],
        "expected": row["expected"],
        "status": row["status"],
        "status_label": row["status_label"],
        "confidence": row["confidence"],
    }


def _check_rows(checks: list[dict]) -> list[dict]:
    rows = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        metric = check.get("metric") or check.get("name")
        rows.append(
            {
                "metric": metric,
                "metric_label": human_metric_name(metric) if metric else "",
                "before": _maybe_float(check.get("before")),
                "after": _maybe_float(check.get("after")),
                "delta": _maybe_float(
                    check.get("absolute_delta") or check.get("delta")
                ),
                "pct_change": _maybe_float(check.get("pct_change")),
                "status": check.get("status"),
                "status_label": _status_label(check.get("status")),
                "status_tone": _status_tone(check.get("status")),
                "confidence": check.get("confidence") or "",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Headline / dek / target description
# ---------------------------------------------------------------------------


def _target_descriptor(target: dict) -> str:
    """Best-effort human descriptor for the target of the control.

    Tries common identifier fields in priority order. Falls back to a
    deterministic ``key=value`` join so a producer that emits a new key
    still produces something readable.
    """
    if not isinstance(target, dict):
        return ""
    for key in ("policy_id", "feature", "rule_id", "name", "identifier"):
        value = target.get(key)
        if value:
            return str(value)
    parts = [f"{k}={v}" for k, v in sorted(target.items()) if v not in (None, "")]
    return ", ".join(parts)


def _cluster_label(scope: dict) -> str:
    """Display-friendly cluster name for the H1. Mirrors
    ``executive_posture._cluster_label``."""
    cluster = scope.get("cluster")
    if cluster:
        return cluster_display(cluster)
    host = scope.get("request_host") or scope.get("entity") or ""
    return host or ""


def _headline(target_descriptor: str, cluster_label: str, after: dict) -> str:
    after_short = _short_window(after)
    pieces = ["Control Review"]
    if target_descriptor:
        pieces.append(f"— {target_descriptor}")
    if cluster_label:
        pieces.append(f"· {cluster_label}")
    if after_short and after_short != "n/a":
        pieces.append(f"· window ending {after_short}")
    return " ".join(pieces)


def _dek(effects: list[dict], target_descriptor: str) -> str:
    """One-sentence elevator pitch for the report. Always grounded in
    what's measurable from the artifact alone — no causal claim."""
    if not effects:
        return (
            "No target effects recorded for this control. "
            "Inspect the artifact metadata below for the windows compared."
        )
    n = len(effects)
    metric_word = "metric" if n == 1 else "metrics"
    target_clause = f" for {target_descriptor}" if target_descriptor else ""
    return (
        f"Effectiveness review across {n} {metric_word}{target_clause}, "
        "comparing the after-window against the expected baseline."
    )


# ---------------------------------------------------------------------------
# Status / confidence labelling
# ---------------------------------------------------------------------------


_STATUS_LABELS = {
    "increased": "Increased",
    "decreased": "Decreased",
    "flat": "Flat",
    "unchanged": "Unchanged",
    "improved": "Improved",
    "worsened": "Worsened",
}

_STATUS_TONES = {
    # The tone classes are styling hints, not semantic verdicts —
    # ``status`` in a control_review is an observation, not a judgment.
    "increased": "monitor",
    "improved": "observe",
    "decreased": "observe",
    "flat": "muted",
    "unchanged": "muted",
    "worsened": "escalate",
}


def _status_label(status: str | None) -> str:
    if not status:
        return ""
    return _STATUS_LABELS.get(status, status.replace("_", " ").capitalize())


def _status_tone(status: str | None) -> str:
    if not status:
        return "muted"
    return _STATUS_TONES.get(status, "muted")


_EXPECTED_BASIS_LABELS = {
    "explicit_target": "Explicit target",
    "previous_window": "Previous window",
    "rolling_baseline": "Rolling baseline",
    "external_model": "External model",
    "before_window": "Before window",
}


def _expected_basis_label(basis: str | None) -> str:
    if not basis:
        return ""
    return _EXPECTED_BASIS_LABELS.get(basis, basis.replace("_", " ").capitalize())


# ---------------------------------------------------------------------------
# Findings synthesis
# ---------------------------------------------------------------------------


def _findings(
    effects: list[dict],
    target_descriptor: str,
    expected_basis: str | None,
) -> list[Finding]:
    """Build the executive-summary findings list.

    The headline finding describes the dominant effect (largest absolute
    delta-vs-expected). Caveats remind the reader this is not a causal
    claim — same language the legacy markdown emits at the bottom of
    ``md_control``.
    """
    if not effects:
        return [
            Finding(
                finding_id="control_review_no_effects",
                title="No effects to report",
                headline="No target effects were emitted for this control",
                body=(
                    "The artifact carries no target_effects. Inspect the "
                    "windows compared and the expected basis below."
                ),
                recommendation=(
                    "Re-run the control review with a non-empty effects set, "
                    "or document why effects could not be computed."
                ),
                caveat=(
                    "Cannot claim the control caused or failed to cause "
                    "movement without measurable effects."
                ),
            )
        ]

    dominant = max(
        effects,
        key=lambda e: abs(_maybe_float(e.get("absolute_delta_vs_expected")) or 0.0),
    )
    metric_label = dominant.get("metric_label") or dominant.get("metric") or "metric"
    delta = dominant.get("absolute_delta_vs_expected")
    pct = dominant.get("pct_change_vs_expected")
    direction = dominant.get("status") or dominant.get("direction") or "moved"
    target_clause = f" for {target_descriptor}" if target_descriptor else ""

    delta_clause_parts = []
    if delta is not None:
        delta_clause_parts.append(f"absolute delta {delta:+.2f} vs expected")
    if pct is not None:
        delta_clause_parts.append(f"{pct:+.2f}% vs expected")
    delta_clause = ", ".join(delta_clause_parts) or "no delta measured"

    basis_clause = ""
    if expected_basis:
        basis_clause = (
            f" Expected basis: {_expected_basis_label(expected_basis).lower()}."
        )

    return [
        Finding(
            finding_id="control_review_dominant_effect",
            title="Dominant effect",
            headline=(
                f"{metric_label} {direction}{target_clause} ({delta_clause})"
            ),
            body=(
                "This control review compares the after-window against the "
                "expected baseline.  Per-metric direction and magnitude appear "
                f"in the effects table below.{basis_clause}"
            ),
            recommendation=(
                "Cross-check this movement against external change "
                "evidence before concluding the control caused it."
            ),
            caveat=(
                "Movement here is descriptive, not causal — concurrent "
                "changes can confound the read."
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def _maybe_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _short_window(window: dict) -> str:
    """``2026-04-08 → 2026-04-15`` from ``{start, end}`` ISO timestamps.

    Mirrors ``executive_posture._short_window`` so cross-report headlines
    read consistently.
    """
    if not window:
        return "n/a"
    start = window.get("start") or ""
    end = window.get("end") or ""

    def _date(value: str) -> str:
        if "T" in value:
            return value.split("T", 1)[0]
        return value

    return f"{_date(start)} → {_date(end)}".strip(" →")
