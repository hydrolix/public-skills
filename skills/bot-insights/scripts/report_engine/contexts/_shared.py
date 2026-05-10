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
"""

from __future__ import annotations

from collections import Counter

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
    plural = f"{noun}s" if not noun.endswith("s") else noun
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
    """Project a triggered rule into the shape the card template renders."""
    return {
        "name": rule.get("name") or "",
        "name_label": humanize_identifier(rule.get("name") or ""),
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
