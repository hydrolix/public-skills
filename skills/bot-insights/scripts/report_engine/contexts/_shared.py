"""Shared helpers used by soc_triage, crawler_governance, and edge_ops_impact.

All functions here are byte-for-byte identical across those three context
modules (differing only in docstrings/comments that were dropped during
copy). Extracting them eliminates the triplicate and brings each context
module under the 500-line ceiling.

The public API of each context module is unchanged — they re-export
nothing from here; they simply import the helpers they need.

Note: ``_entity_actions`` is intentionally kept per-module because it
calls each module's own ``_entity_display``, which varies between SOC
(ASN prefix), crawler/edge (ai_category humanization), and a common
host/IP passthrough. Extracting it would require passing
``_entity_display`` as a parameter, changing the internal call sites
without benefit.

Companion-artifact selection (``companion_compatible``,
``select_control_companions``) was extracted from ``render_report.py``
in M1.2 so the engine ``assemble()`` paths can validate companion
artifacts without importing from the legacy renderer module. The legacy
renderer re-exports these helpers so its callers continue to work.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from .. import scorecards as scorecards_mod
from .. import verdicts as verdicts_mod
from ..formatters import format_share_pct
from ..humanize import humanize_identifier
from ..theme import DOMAIN_LABELS

# ---------------------------------------------------------------------------
# Module-level constant (identical in all three context modules)
# ---------------------------------------------------------------------------

_QUEUE_ORDER = {state: i for i, state in enumerate(verdicts_mod.STATE_ORDER)}


# ---------------------------------------------------------------------------
# Shadow helpers (degraded mode — no per-entity scorecards available)
# ---------------------------------------------------------------------------


def _shadow_scorecard(entry: dict, index: dict) -> dict:
    """Project a ``ranked_entities`` entry into a minimal scorecard shape.

    Used only when the wrapper carried no per-entity scorecards. The
    shadow card carries empty ``rule_results`` — verdict classification
    in degraded mode runs through ``_shadow_verdict`` instead of the
    rule-count-driven classifier, so monitor-tier producer rankings
    don't collapse to ``close_as_expected``.
    """
    band = entry.get("band") or "observe"
    primary = entry.get("primary_domain") or "none"
    return {
        "schema_version": "bot_entity_scorecard.v1",
        "entity": entry.get("entity"),
        "entity_type": entry.get("entity_type"),
        "score": entry.get("score"),
        "band": band,
        "confidence": entry.get("confidence") or "low",
        "primary_domain": primary,
        "scope": index.get("scope") or {},
        "table_used": index.get("table_used"),
        "rule_results": [],
        "features": [],
        "not_evaluated_features": [],
        "evidence_summary": [],
        "recommended_next_steps": [],
        "domain_scores": {},
        "score_delta_points": 0,
    }


def _shadow_verdict(band: str) -> dict:
    """Verdict for a shadow scorecard derived from the producer's band.

    Bypasses the rule-count classifier — there are no rule_results in
    degraded mode, so the rule-count-zero branch would treat every
    entity as clean. Map the band straight to the canonical state:
    escalate / monitor bands → Assign, observe → Close — expected.
    """
    if band in verdicts_mod.ESCALATE_BANDS:
        return {
            "state": "assign",
            "label": "Assign",
            "tone": "escalate",
            "rationale": (
                f"Producer ranked this entity in the {band.replace('_', ' ')} "
                "band. Per-rule evidence absent in this report."
            ),
        }
    if band in verdicts_mod.MONITOR_BANDS:
        return {
            "state": "assign",
            "label": "Assign",
            "tone": "monitor",
            "rationale": (
                f"Producer ranked this entity in the {band.replace('_', ' ')} "
                "band. Per-rule evidence absent in this report."
            ),
        }
    return {
        "state": "close_as_expected",
        "label": "Close — expected",
        "tone": "observe",
        "rationale": "Producer ranked in observe band; no rule data to override.",
    }


# ---------------------------------------------------------------------------
# Queue / triage helpers
# ---------------------------------------------------------------------------


def _queue_rows(entities: list[dict]) -> list[dict]:
    """Sort by verdict state first, then by score descending (SOC scoring
    convention is high score = high risk), then by producer rank as a
    final tiebreaker.
    """
    return sorted(
        entities,
        key=lambda e: (
            _QUEUE_ORDER.get(e.get("verdict_state", "watch"), 99),
            -(e.get("score") or 0),
            e.get("rank", 999) if isinstance(e.get("rank"), int) else 999,
        ),
    )


def _triage_strip(
    verdicts_by_entity: dict[str, dict],
    n_total: int,
    entity_type_label: str,
    entity_type_label_plural: str | None = None,
) -> dict:
    """Aggregate per-entity verdicts into the triage strip. Same shape the
    scorecard brief produces; the rationale uses the entity-type noun
    instead of "host".
    """
    state_counts = {state: 0 for state in verdicts_mod.STATE_ORDER}
    for v in verdicts_by_entity.values():
        state_counts[v["state"]] = state_counts.get(v["state"], 0) + 1

    pills = [
        {
            "state": state,
            "label": verdicts_mod.STATE_LABELS[state],
            "tone": verdicts_mod.STATE_TONE[state],
            "count": state_counts.get(state, 0),
        }
        for state in verdicts_mod.STATE_ORDER
    ]

    n_assign = state_counts.get("assign", 0)
    n_watch = state_counts.get("watch", 0)
    n_close = state_counts.get("close_as_expected", 0)
    n_insufficient = state_counts.get("insufficient_data", 0)

    noun = entity_type_label or "entity"
    plural = entity_type_label_plural or (
        noun if noun.endswith("s") else f"{noun}s"
    )
    parts: list[str] = []
    if n_assign:
        verb = "needs" if n_assign == 1 else "need"
        parts.append(
            f"{n_assign} {noun if n_assign == 1 else plural} {verb} analyst attention"
        )
    if n_watch:
        parts.append(f"{n_watch} to watch")
    if n_insufficient:
        parts.append(f"{n_insufficient} cannot be judged from this report alone")
    if not parts and n_close:
        parts.append(f"all {n_close} {noun if n_close == 1 else plural} read clean")
    rationale = (
        "; ".join(parts) + (f" (out of {n_total})." if n_total else ".")
        if parts
        else ""
    )

    return {
        "pills": pills,
        "rationale": rationale,
        "counts": state_counts,
    }


# ---------------------------------------------------------------------------
# Coverage / scoring helpers
# ---------------------------------------------------------------------------


def _aggregate_coverage(scorecards: list[dict]) -> dict[str, dict[str, int]]:
    """Per-domain triggered/below/missing counts across the fleet."""
    coverage: dict[str, Counter] = {}
    for sc in scorecards:
        for rule in scorecards_mod.normalize_rule_results(sc):
            domain = rule.get("domain") or "other"
            status = rule.get("status") or "missing_input"
            coverage.setdefault(domain, Counter())[status] += 1
    return {d: dict(c) for d, c in coverage.items()}


def _feature_row(rule: dict) -> dict:
    """Project a triggered rule into the shape the card template renders.

    ``name_label`` uses the ``_RULE_LABEL_PARTS`` mapping (via
    ``display_label``) so known rules render with proper acronym
    preservation — e.g. ``ai_crawler_growth_high`` becomes
    "AI Crawler Growth High" rather than "Ai crawler growth high".
    Unknown rules fall back to title-cased identifiers with acronyms
    upper-cased.
    """
    from ..humanize import display_label

    return {
        "name": rule.get("name") or "",
        "name_label": display_label(rule.get("name") or ""),
        "domain": rule.get("domain") or "",
        "domain_label": DOMAIN_LABELS.get(
            rule.get("domain") or "", rule.get("domain") or ""
        ),
        "points": rule.get("points"),
        "current": rule.get("current"),
        "baseline": rule.get("baseline"),
        "threshold": rule.get("threshold"),
        "evidence": rule.get("evidence") or "",
        "supporting_metrics": rule.get("supporting_metrics") or {},
    }


def _matrix_cell_tone(value: object) -> str:
    """Map a domain-score cell value to a pill tone.

    The thresholds mirror the band cutoffs in ``theme.BAND_THRESHOLDS``
    rather than introducing a new scale: ≥ 30 points in one domain reads
    as escalate-tinted (a single domain pushed the entity's overall band
    down), 1–29 reads as monitor-tinted, 0 stays neutral.
    """
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "neutral"
    if v >= 30:
        return "escalate"
    if v > 0:
        return "monitor"
    return "neutral"


def _scorecard_rollup(entities: list[dict]) -> list[dict]:
    """Thin per-entity rollup for the embedded_scorecards macro.

    Keeps Score / Band / Verdict alongside the raw entity identifier so
    the rollup section hands the analyst a single-row view that mirrors
    the queue table's ordering without the per-rule cards.
    """
    return [
        {
            "entity": e.get("entity_display") or e.get("entity"),
            "score": e.get("score"),
            "band": e.get("band"),
            "verdict_label": e.get("verdict_label"),
            "verdict_tone": e.get("verdict_tone"),
        }
        for e in entities
    ]


# ---------------------------------------------------------------------------
# Top-assign card and traffic-share helpers
# ---------------------------------------------------------------------------


def _top_assign_card(queue_rows: list[dict], scorecards: list[dict]) -> dict | None:
    """The top Assign entity (or top Watch when no Assign exists),
    bundled with its source scorecard for downstream synthesis.
    """
    target_state = None
    for r in queue_rows:
        if r.get("verdict_state") == "assign":
            target_state = "assign"
            break
    if target_state is None:
        for r in queue_rows:
            if r.get("verdict_state") == "watch":
                target_state = "watch"
                break
    if target_state is None:
        return None
    target_row = next(
        (r for r in queue_rows if r.get("verdict_state") == target_state),
        None,
    )
    if target_row is None:
        return None
    sc = next(
        (s for s in scorecards if s.get("entity") == target_row.get("entity")),
        None,
    )
    if sc is None:
        return None
    return {
        "row": target_row,
        "scorecard": sc,
        "entity_display": target_row.get("entity_display") or sc.get("entity"),
    }


def _traffic_share_clause(sc: dict, scorecards: list[dict], n_total: int) -> str:
    """Compute the top entity's share of fleet requests when every
    scorecard carries ``entity_metrics.current_requests``. Omitted when
    any scorecard is missing volume — same don't-fabricate rule the brief
    uses for shared_signal.
    """
    if n_total < 1:
        return ""
    requests: list[float] = []
    for s in scorecards:
        em = s.get("entity_metrics") or {}
        cur = em.get("current_requests")
        if cur is None:
            return ""
        try:
            requests.append(float(cur))
        except (TypeError, ValueError):
            return ""
    fleet = sum(requests)
    if fleet <= 0:
        return ""
    em = sc.get("entity_metrics") or {}
    cur = em.get("current_requests")
    if cur is None:
        return ""
    try:
        share = float(cur) / fleet * 100.0
    except (TypeError, ValueError):
        return ""
    return f"covers {format_share_pct(share)} of fleet requests this window"


# ---------------------------------------------------------------------------
# Companion-artifact selection (M1.2 extraction from render_report.py)
# ---------------------------------------------------------------------------


CONTROL_SCHEMA = "bot_control_review.v1"
POSTURE_SCHEMA = "bot_posture_movement.v1"
MOVER_SCHEMA = "bot_mover_attribution.v1"
TIMESERIES_SCHEMA = "bot_timeseries.v1"

COMPANION_COMPAT_FIELDS = (
    "scope",
    "current_window",
    "baseline_windows",
    "comparison_type",
    "table_used",
)


def known(value: Any) -> bool:
    """``True`` if ``value`` is a non-empty, non-null artifact field.

    Used to gate companion-compatibility checks: a missing field on
    either side disqualifies the companion (cannot prove equivalence).
    """
    return value not in (None, "", [], {})


def companion_compatible(
    primary: dict[str, Any] | None,
    companion: dict[str, Any],
) -> tuple[bool, str | None]:
    """Return ``(ok, reason)`` for pairing ``companion`` with ``primary``.

    Both artifacts must agree on every field in ``COMPANION_COMPAT_FIELDS``.
    A field missing on either side is treated as a compatibility failure
    rather than a silent pass — the report can't prove the companion
    describes the same scope/window.
    """
    if primary is None:
        return False, "no primary artifact carries compatibility metadata"
    for field in COMPANION_COMPAT_FIELDS:
        left = primary.get(field)
        right = companion.get(field)
        if not known(left) or not known(right):
            return False, f"missing {field} metadata on one side"
        if left != right:
            return False, f"conflict on {field}"
    return True, None


def select_control_companions(
    artifacts: list[dict[str, Any]],
    *,
    warn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Pick the control review primary plus its optional companion artifacts.

    Returns a dict with these keys:

    * ``"control"``: the single required ``bot_control_review.v1`` artifact.
    * ``"posture"``: optional ``bot_posture_movement.v1`` if companion-
      compatible with the control; ``None`` otherwise (with a warning).
    * ``"mover"``: optional ``bot_mover_attribution.v1`` with the same
      compatibility rule.
    * ``"timeseries"``: optional ``bot_timeseries.v1`` with the same rule.

    Multiple control / posture / mover / timeseries artifacts in the
    input list are an error: the caller must dedupe upstream. ``warn``
    is invoked once per dropped companion with the
    ``"Omitting optional ... from combined sections: ..."`` message the
    legacy renderer emits, so parity is preserved.
    """
    controls = [a for a in artifacts if a.get("schema_version") == CONTROL_SCHEMA]
    if not controls:
        raise ValueError(
            "control_review wrapper missing bot_control_review.v1 artifact"
        )
    if len(controls) > 1:
        raise ValueError(
            "control_review wrapper cannot select between multiple "
            "bot_control_review.v1 artifacts"
        )
    control = controls[0]

    def _single(schema: str, label: str) -> dict[str, Any] | None:
        matches = [a for a in artifacts if a.get("schema_version") == schema]
        if len(matches) > 1:
            raise ValueError(
                f"control_review cannot select between multiple {schema} artifacts"
            )
        return matches[0] if matches else None

    def _filter(
        companion: dict[str, Any] | None,
        label: str,
    ) -> dict[str, Any] | None:
        if companion is None:
            return None
        ok, reason = companion_compatible(control, companion)
        if ok:
            return companion
        if warn is not None:
            warn(
                "Omitting optional "
                f"{label} {companion.get('artifact_id')} "
                f"from combined sections: {reason}."
            )
        return None

    posture = _filter(_single(POSTURE_SCHEMA, "posture"), "posture")
    mover = _filter(_single(MOVER_SCHEMA, "mover"), "mover")
    timeseries_raw = _single(TIMESERIES_SCHEMA, "timeseries")
    timeseries = _filter(timeseries_raw, "timeseries") if timeseries_raw else None

    return {
        "control": control,
        "posture": posture,
        "mover": mover,
        "timeseries": timeseries,
    }
