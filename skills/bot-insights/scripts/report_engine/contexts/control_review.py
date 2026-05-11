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
    findings = _findings(
        effects,
        target_descriptor,
        expected_basis,
        collateral_checks,
        displacement_checks,
    )

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
    collateral_checks: list[dict] | None = None,
    displacement_checks: list[dict] | None = None,
) -> list[Finding]:
    """Build the executive-summary findings list.

    Headline names the deterministic verdict — *on target*, *overshoot*,
    *under-delivered*, *side effects flagged*, or *inconclusive* — derived
    from |pct vs expected| on the dominant effect plus any movement in
    collateral / displacement checks. Recommendation routes to a concrete
    outcome (continue / monitor / investigate or roll back / regenerate
    evidence).
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
                    "Regenerate evidence with non-empty effects, or document "
                    "why effects could not be computed."
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
    target_clause = f" for {target_descriptor}" if target_descriptor else ""

    verdict, verdict_phrase, recommendation = _classify_verdict(
        dominant, collateral_checks or [], displacement_checks or []
    )

    delta_clause_parts = []
    if delta is not None:
        delta_clause_parts.append(f"absolute delta {delta:+.2f} vs expected")
    if pct is not None:
        delta_clause_parts.append(f"{pct:+.2f}% vs expected")
    delta_clause = "; ".join(delta_clause_parts)

    headline = f"{verdict_phrase} on {metric_label}{target_clause}"
    if delta_clause:
        headline += f" ({delta_clause})"

    basis_clause = ""
    if expected_basis:
        basis_clause = (
            f" Expected basis: {_expected_basis_label(expected_basis).lower()}."
        )

    body_parts = [
        "Movement compared against the expected baseline. Per-metric "
        "direction and magnitude appear in the effects table below."
    ]
    if basis_clause:
        body_parts.append(basis_clause.strip())
    side_effect_note = _side_effect_note(
        collateral_checks or [], displacement_checks or []
    )
    if side_effect_note:
        body_parts.append(side_effect_note)
    body = " ".join(body_parts)

    caveat_parts = [
        "Movement is descriptive, not causal — concurrent changes can "
        "confound the read."
    ]
    if _has_missing_side_effect_deltas(
        collateral_checks or [], displacement_checks or []
    ):
        caveat_parts.append(
            "Collateral or displacement deltas are unavailable; side-effect "
            "magnitude cannot be quantified from this evidence alone."
        )
    caveat = " ".join(caveat_parts)

    return [
        Finding(
            finding_id=f"control_review_{verdict}",
            title="Verdict",
            headline=headline,
            body=body,
            recommendation=recommendation,
            caveat=caveat,
        )
    ]


_OVERSHOOT_PCT = 50.0
_UNDER_DELIVERED_PCT = 25.0


def _classify_verdict(
    dominant: dict,
    collateral_checks: list[dict],
    displacement_checks: list[dict],
) -> tuple[str, str, str]:
    """Return ``(verdict_id, headline_phrase, recommendation)``.

    Five outcomes:
    - ``inconclusive`` — dominant effect has no delta vs expected.
    - ``side_effects_flagged`` — dominant tracks expected (within
      ``_UNDER_DELIVERED_PCT``) but collateral or displacement moved.
    - ``on_target`` — dominant tracks expected and no side effects moved.
    - ``overshoot`` — dominant pct vs expected exceeds ``_OVERSHOOT_PCT``.
    - ``under_delivered`` — dominant pct vs expected is below
      ``-_UNDER_DELIVERED_PCT``.
    """
    pct = _maybe_float(dominant.get("pct_change_vs_expected"))
    if pct is None:
        return (
            "inconclusive",
            "Inconclusive",
            "Regenerate evidence with comparable windows and a populated "
            "expected basis before deciding next steps.",
        )

    side_effects_moved = _any_side_effect_moved(
        collateral_checks, displacement_checks
    )

    if pct > _OVERSHOOT_PCT:
        return (
            "overshoot",
            "Overshoot vs expected",
            "Investigate the magnitude before letting the control ride; "
            "consider rolling back or tightening if side effects are "
            "material.",
        )
    if pct < -_UNDER_DELIVERED_PCT:
        return (
            "under_delivered",
            "Under-delivered vs expected",
            "Verify the control reached the intended traffic; tune or "
            "extend the policy if the gap is operationally meaningful.",
        )
    if side_effects_moved:
        return (
            "side_effects_flagged",
            "On expected magnitude with side effects",
            "Monitor — confirm collateral / displacement movement is within "
            "tolerance before extending or widening the control.",
        )
    return (
        "on_target",
        "On target",
        "Continue monitoring; no immediate action required if side-effect "
        "checks remain clean.",
    )


def _any_side_effect_moved(
    collateral_checks: list[dict],
    displacement_checks: list[dict],
) -> bool:
    """True when any collateral or displacement row reports a non-flat
    ``status`` (e.g. ``increased`` / ``decreased``).

    Used by the verdict classifier to flag side effects regardless of
    whether the producer emitted numeric deltas.
    """
    for row in (*collateral_checks, *displacement_checks):
        status = (row.get("status") or "").lower()
        if status and status not in {"unchanged", "stable", "flat", ""}:
            return True
    return False


def _has_missing_side_effect_deltas(
    collateral_checks: list[dict],
    displacement_checks: list[dict],
) -> bool:
    """True when any collateral / displacement row has a movement status
    but the numeric delta is unavailable. Used to qualify the caveat.
    """
    for row in (*collateral_checks, *displacement_checks):
        status = (row.get("status") or "").lower()
        if status and status not in {"unchanged", "stable", "flat", ""}:
            if row.get("delta") is None and row.get("pct_change") is None:
                return True
    return False


def _side_effect_note(
    collateral_checks: list[dict],
    displacement_checks: list[dict],
) -> str:
    """One-line summary of side-effect movement for the body paragraph."""
    moved_collateral = [
        r for r in collateral_checks
        if (r.get("status") or "").lower() not in {"unchanged", "stable", "flat", ""}
    ]
    moved_displacement = [
        r for r in displacement_checks
        if (r.get("status") or "").lower() not in {"unchanged", "stable", "flat", ""}
    ]
    parts: list[str] = []
    if moved_collateral:
        parts.append(
            f"{len(moved_collateral)} collateral check"
            f"{'s' if len(moved_collateral) != 1 else ''} moved"
        )
    if moved_displacement:
        parts.append(
            f"{len(moved_displacement)} displacement check"
            f"{'s' if len(moved_displacement) != 1 else ''} moved"
        )
    if not parts:
        return ""
    return "Side-effect checks: " + " and ".join(parts) + "."


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
