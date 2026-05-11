"""Context preparer for the SOC triage report.

A SOC analyst lens over the same ``bot_scorecard_artifacts.v1`` packet
that ``scorecard_brief`` reads, but reframed:

- The fleet axis is whatever entity dimension the producer ranked on
  (typically ``client_asn``), not request hosts.
- Lead with the security-evidence domain — bad-bot share, SIEM auth-fail
  and blocked counts — then volume movement as a secondary lens.
- Show a per-entity domain score matrix so the analyst can see where
  the points landed without reading every triggered-feature card.

The wrapper's ``report_type: "soc_triage"`` routes here. The packet's
``schema_version: "bot_scorecard_artifacts.v1"`` keeps its
schema-registry mapping to ``scorecard_brief`` for raw-artifact mode —
SOC is a wrapper-only report.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from .. import scorecards as scorecards_mod
from .. import verdicts as verdicts_mod
from ..findings import Finding
from ..humanize import (
    cluster_display,
    humanize_entity_type,
    humanize_entity_type_plural,
    humanize_identifier,
    title_case_label,
)
from ..theme import DOMAIN_LABELS, DOMAIN_ORDER
from ._shared import (
    _aggregate_coverage,
    _feature_row,
    _matrix_cell_tone,
    _queue_rows,
    _shadow_scorecard,
    _shadow_verdict,
    _top_assign_card,
    _traffic_share_clause,
    _triage_strip,
)
from .scorecard_brief import (
    _aggregate_actions,
    _entity_row,
)

SCHEMA = "bot_scorecard_artifacts.v1"
REPORT_TYPE = "soc_triage"
TEMPLATE = "reports/soc_triage.html"

# Wrapper analyst-note routing. Same slot names the other contexts use so
# producers don't have to special-case SOC.
NOTE_ID_TO_SLOT = {
    "llm-interpretation": "executive_summary",
    "llm-operational": "operational_interpretation",
    "llm-finding-overrides": "finding_overrides",
}

PURPOSE = {
    "kicker": "Bot Insights — security risk triage",
    "measures": (
        "A risk score for each ranked entity (typically ASN) on a 0–100 "
        "scale. Higher scores reflect more triggered security signals — "
        "bad-bot share, SIEM auth failures, SIEM blocked requests — plus "
        "movement-side context."
    ),
    "score_legend": (
        "Higher score = more triggered security/movement rules. "
        "Bands: escalate, monitor, observe."
    ),
    "cant_say": (
        "Not a confirmed-malicious determination. Missing inputs are "
        "reported as missing — they are not scored as safe."
    ),
}

# Security-evidence rules, ranked by how strongly they imply analyst action.
# Used to pick the lead clause when the top entity's primary domain is
# ``security_evidence``.
_SECURITY_RULE_ORDER = (
    "bad_bot_share_high",
    "siem_auth_fail_present",
    "siem_blocked_present",
    "siem_authfail_blocked_concentration",
)


def assemble(artifacts: list[dict]) -> dict:
    """Reassemble a ``bot_report_input.v1`` wrapper's artifacts into the dict
    shape ``prepare()`` expects.

    Accepts both shapes the producer may emit:
    - Bundled: a single ``bot_scorecard_artifacts.v1`` packet that nests
      ``index`` + ``scorecards``. This is the shape the SOC fixture uses.
    - Flat: separate ``bot_scorecard_index.v1`` and a list of
      ``bot_entity_scorecard.v1`` entries. Same fallback the brief uses.
    """
    packet = next(
        (
            a
            for a in artifacts
            if a.get("schema_version") == "bot_scorecard_artifacts.v1"
        ),
        None,
    )
    if packet is not None:
        index = packet.get("index")
        scorecards = list(packet.get("scorecards") or [])
        producer_limit = packet.get("producer_limit")
        result_truncated = packet.get("result_truncated", False)
        total_ranked = packet.get("total_ranked_entities")
    else:
        index = next(
            (
                a
                for a in artifacts
                if a.get("schema_version") == "bot_scorecard_index.v1"
            ),
            None,
        )
        scorecards = [
            a for a in artifacts if a.get("schema_version") == "bot_entity_scorecard.v1"
        ]
        producer_limit = (index or {}).get("producer_limit")
        result_truncated = False
        total_ranked = None

    if index is None:
        raise ValueError("soc_triage wrapper missing bot_scorecard_index.v1 artifact")

    if total_ranked is None:
        total_ranked = len(index.get("ranked_entities") or [])

    return {
        "schema_version": SCHEMA,
        "index": index,
        "scorecards": scorecards,
        "producer_limit": producer_limit,
        "result_row_count": len(scorecards),
        "result_truncated": result_truncated,
        "total_ranked_entities": total_ranked,
    }


def prepare(artifact: dict) -> dict:
    index = artifact["index"]
    scorecards = artifact.get("scorecards") or []
    ranked_entities = index.get("ranked_entities") or []

    # Degraded fallback: wrapper carries the ranking but no scorecard
    # artifacts. Build minimal shadow cards from the index so the queue
    # table still renders, even though every per-rule view (security
    # evidence, domain matrix, actions) lands empty.
    degraded = not scorecards and bool(ranked_entities)
    if degraded:
        scorecards = [_shadow_scorecard(e, index) for e in ranked_entities]

    scope = (scorecards[0]["scope"] if scorecards else index.get("scope")) or {}
    scope_host = scope.get("request_host") or ""
    cluster = scope.get("cluster") or ""
    cluster_label = cluster_display(cluster) if cluster else (scope_host or "")
    table_used = (scorecards[0].get("table_used") if scorecards else None) or (
        index.get("table_used") or ""
    )

    entity_type = _resolve_entity_type(ranked_entities, scorecards)
    entity_type_label = humanize_entity_type(entity_type)
    entity_type_label_plural = humanize_entity_type_plural(entity_type)
    entity_type_label_title = title_case_label(entity_type_label)

    n_total = len(scorecards)
    rank_lookup = {e.get("entity"): e.get("rank") for e in ranked_entities}

    # Per-entity verdicts via the shared 4-state classifier (or a
    # band-only fallback in degraded mode where rule_results is empty).
    verdicts_by_entity: dict[str, dict] = {}
    for sc in scorecards:
        if degraded:
            verdicts_by_entity[sc["entity"]] = _shadow_verdict(sc["band"])
        else:
            rc = scorecards_mod.rule_counts(sc)
            verdicts_by_entity[sc["entity"]] = verdicts_mod.classify(sc["band"], rc)

    entities = [_entity_row(sc, rank_lookup) for sc in scorecards]
    for e in entities:
        v = verdicts_by_entity.get(e["entity"])
        e["verdict"] = v
        e["verdict_state"] = v["state"] if v else "watch"
        e["verdict_label"] = v["label"] if v else "Watch"
        e["verdict_tone"] = v["tone"] if v else "monitor"
        e["entity_type"] = entity_type
        e["entity_type_label"] = entity_type_label
        e["entity_display"] = _entity_display(e["entity"], entity_type)

    queue_rows = _queue_rows(entities)
    triage_strip = _triage_strip(
        verdicts_by_entity, n_total, entity_type_label, entity_type_label_plural
    )
    coverage = _aggregate_coverage(scorecards)
    actions = _aggregate_actions(scorecards)

    # Degraded mode (no producer scorecards) cannot populate security cards
    # or the domain matrix — the synthetic shadow rule_results carry no
    # per-feature evidence, only enough state to drive band-based verdicts.
    domain_matrix: dict
    if degraded:
        security_cards: list[dict] = []
        domain_matrix = {"domains": [], "rows": []}
    else:
        security_cards = _security_evidence_cards(
            scorecards, verdicts_by_entity, rank_lookup, entity_type
        )
        domain_matrix = _domain_score_matrix(scorecards, rank_lookup, entity_type)

    actionable = _actionable_summary(
        scorecards,
        queue_rows,
        triage_strip,
        actions,
        coverage,
        n_total,
        entity_type_label,
        entity_type_label_plural,
    )
    findings = [actionable]

    confidence_counts = Counter(sc.get("confidence") or "low" for sc in scorecards)
    confidence_reasons: set[str] = set()
    for sc in scorecards:
        confidence_reasons.update(sc.get("confidence_reasons") or [])

    # Bot Insights — security risk triage / SOC Triage — <cluster>, <entity_type_label> risk queue
    headline_scope = cluster_label or scope_host or "fleet"
    headline = f"SOC Triage — {headline_scope}, {entity_type_label} risk queue"
    dek = "Top entities ranked by mechanical risk indicators for the current window."

    # Index falls back to first scorecard's window when absent (degraded
    # fixture path) so the comparison strip still renders.
    current_window = index.get("current_window") or (
        scorecards[0].get("current_window") if scorecards else None
    )
    baseline_windows = index.get("baseline_windows") or (
        scorecards[0].get("baseline_windows") if scorecards else None
    )
    windows = None
    if current_window and baseline_windows:
        windows = {
            "current": current_window,
            "baseline": baseline_windows[0],
        }

    return {
        "title": "SOC Triage",
        "kicker": PURPOSE["kicker"],
        "headline": headline,
        "dek": dek,
        "purpose": None,
        "orientation": {
            "measures": PURPOSE["measures"],
            "score_legend": PURPOSE["score_legend"],
            "cant_say": PURPOSE["cant_say"],
        },
        "scope": {
            "cluster": cluster,
            "database": scope.get("database") or "",
            "table_used": table_used,
            "request_host": scope_host,
        },
        "windows": windows,
        "entity_type": entity_type,
        "entity_type_label": entity_type_label,
        "entity_type_label_plural": entity_type_label_plural,
        "entity_type_label_title": entity_type_label_title,
        "degraded": degraded,
        "triage_strip": triage_strip,
        "queue_rows": queue_rows,
        "security_cards": security_cards,
        "domain_matrix": domain_matrix,
        "actions": _entity_actions(scorecards, entity_type),
        "aggregated_actions": actions,
        "coverage_rows": _coverage_rows(coverage),
        "findings": findings,
        "method": {
            "schema_version": artifact.get("schema_version"),
            "comparison_type": index.get("comparison_type"),
            "producer_limit": index.get("producer_limit")
            or artifact.get("producer_limit"),
            "result_row_count": artifact.get("result_row_count"),
            "result_truncated": artifact.get("result_truncated"),
            "interpretation_constraints": index.get("interpretation_constraints") or [],
        },
        "confidence": {
            "counts": dict(confidence_counts),
            "reasons": sorted(confidence_reasons),
        },
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# ---- helpers ----------------------------------------------------------------


def _resolve_entity_type(ranked_entities: list[dict], scorecards: list[dict]) -> str:
    """Pick the producer's entity_type axis. All ranked rows in a single
    report share one entity_type; fall back to the first scorecard, then
    to ``client_asn`` so an empty index still labels sensibly.
    """
    for entry in ranked_entities:
        et = entry.get("entity_type")
        if et:
            return et
    for sc in scorecards:
        et = sc.get("entity_type")
        if et:
            return et
    return "client_asn"


def _entity_display(entity: str, entity_type: str) -> str:
    """Reader-facing rendering of an entity identifier.

    For ASNs the bare number reads as a domain name; prepending the noun
    avoids that ambiguity ("64500" → "ASN 64500"). Hosts and IPs render
    as-is — they're already self-evident in the column. snake_case slugs
    get Title Case with acronym preservation.
    """
    from ..humanize import humanize_entity_value
    if not entity:
        return entity
    return humanize_entity_value(entity, entity_type)


def _coverage_rows(coverage: dict[str, dict[str, int]]) -> list[dict]:
    rows: list[dict] = []
    for domain in DOMAIN_ORDER:
        counts = coverage.get(domain) or {}
        triggered = counts.get("triggered", 0)
        evaluated_zero = counts.get("evaluated_zero", 0)
        missing = counts.get("missing_input", 0)
        if triggered + evaluated_zero + missing == 0:
            continue
        rows.append(
            {
                "domain": domain,
                "domain_label": DOMAIN_LABELS.get(domain, domain),
                "triggered": triggered,
                "evaluated_zero": evaluated_zero,
                "missing": missing,
            }
        )
    return rows


def _security_evidence_cards(
    scorecards: list[dict],
    verdicts_by_entity: dict[str, dict],
    rank_lookup: dict[str, int],
    entity_type: str,
) -> list[dict]:
    """Per-entity card for each Assign or Watch entity.

    Each card foregrounds the security_evidence domain — the rules whose
    triggers tell the analyst what to investigate first — then lists
    adjacent triggered features (movement, etc.) below as supporting
    context. Closed-as-expected and Insufficient entities are omitted;
    the queue table covers them.
    """
    cards: list[dict] = []
    actionable = {"assign", "watch"}
    for sc in scorecards:
        verdict = verdicts_by_entity.get(sc["entity"])
        if not verdict or verdict["state"] not in actionable:
            continue
        rules = scorecards_mod.normalize_rule_results(sc)
        triggered = [r for r in rules if r.get("status") == "triggered"]
        if not triggered:
            continue
        sec = [r for r in triggered if r.get("domain") == "security_evidence"]
        other = [r for r in triggered if r.get("domain") != "security_evidence"]
        sec_features = [_feature_row(r) for r in _sort_security_rules(sec)]
        other_features = [
            _feature_row(r)
            for r in sorted(
                other, key=lambda r: (-(r.get("points") or 0), r.get("name") or "")
            )
        ]
        rc = scorecards_mod.rule_counts(sc)
        confidence_chip = verdicts_mod.confidence_chip(rc)
        cards.append(
            {
                "entity": sc["entity"],
                "entity_display": _entity_display(sc["entity"], entity_type),
                "rank": rank_lookup.get(sc["entity"]),
                "score": sc.get("score"),
                "band": sc.get("band"),
                "primary_domain": sc.get("primary_domain"),
                "primary_domain_label": DOMAIN_LABELS.get(
                    sc.get("primary_domain") or "", sc.get("primary_domain") or ""
                ),
                "verdict_state": verdict["state"],
                "verdict_label": verdict["label"],
                "verdict_tone": verdict["tone"],
                "security_features": sec_features,
                "other_features": other_features,
                "confidence_chip": confidence_chip,
                "evidence_summary": sc.get("evidence_summary") or [],
            }
        )

    state_rank = {"assign": 0, "watch": 1}
    cards.sort(
        key=lambda c: (
            state_rank.get(c["verdict_state"], 9),
            -(c.get("score") or 0),
            c.get("rank") or 999,
        )
    )
    return cards


def _sort_security_rules(rules: list[dict]) -> list[dict]:
    """Stable order for the security_evidence rule list inside a card.

    Rules listed in ``_SECURITY_RULE_ORDER`` come first in that order;
    anything else falls in by points descending so a high-points novel
    rule still surfaces near the top.
    """
    priority = {name: i for i, name in enumerate(_SECURITY_RULE_ORDER)}
    return sorted(
        rules,
        key=lambda r: (
            priority.get(r.get("name") or "", len(_SECURITY_RULE_ORDER)),
            -(r.get("points") or 0),
            r.get("name") or "",
        ),
    )


def _domain_score_matrix(
    scorecards: list[dict],
    rank_lookup: dict[str, int],
    entity_type: str,
) -> dict:
    """Entities × domains grid of per-cell point totals.

    The grid lets the analyst see the shape of triggered evidence at a
    glance — does the report concentrate on security_evidence, or did
    movement carry weight too? Cells with no points render as a muted
    dash; non-zero cells render as a tinted pill.
    """
    domains = list(DOMAIN_ORDER)
    rows: list[dict] = []
    for sc in scorecards:
        domain_scores = sc.get("domain_scores") or {}
        cells: list[dict] = []
        for d in domains:
            value = domain_scores.get(d) or 0
            cells.append(
                {
                    "domain": d,
                    "domain_label": DOMAIN_LABELS.get(d, d),
                    "value": int(value) if isinstance(value, (int, float)) else 0,
                    "tone": _matrix_cell_tone(value),
                }
            )
        rows.append(
            {
                "entity": sc.get("entity"),
                "entity_display": _entity_display(sc.get("entity") or "", entity_type),
                "rank": rank_lookup.get(sc.get("entity") or ""),
                "score": sc.get("score"),
                "cells": cells,
            }
        )
    rows.sort(key=lambda r: (-(r.get("score") or 0), r.get("rank") or 999))
    return {
        "domains": [
            {"domain": d, "domain_label": DOMAIN_LABELS.get(d, d)} for d in domains
        ],
        "rows": rows,
    }


def _entity_actions(scorecards: list[dict], entity_type: str) -> list[dict]:
    """Per-entity action rows for the inlined "Recommended next steps"
    section.

    Same shape the executive_posture report consumes: each entry carries
    ``summary`` / ``detail`` plus a preview that names the affected
    entity. Aggregation across entities collapses identical recommendations,
    same rule the brief uses.
    """
    aggregated = _aggregate_actions(scorecards)
    out: list[dict] = []
    for action in aggregated:
        # ``preview`` from _aggregate_actions is a comma-joined entity list.
        # For SOC, prepend the entity-type noun so the preview reads
        # "ASN 64500, ASN 64600" rather than the bare numbers.
        host_count = action.get("host_count") or 0
        preview_entities = (action.get("preview") or "").split(",")
        formatted_preview = ", ".join(
            _entity_display(e.strip(), entity_type)
            for e in preview_entities
            if e.strip()
        )
        out.append(
            {
                "summary": action.get("summary"),
                "detail": action.get("detail") or action.get("step"),
                "step": action.get("step") or action.get("detail"),
                "host_count": host_count,
                "preview": formatted_preview,
                "extra": action.get("extra") or 0,
            }
        )
    return out


def _actionable_summary(
    scorecards: list[dict],
    queue_rows: list[dict],
    triage_strip: dict,
    actions: list[dict],
    coverage: dict[str, dict[str, int]],
    n_total: int,
    entity_type_label: str,
    entity_type_label_plural: str | None = None,
) -> Finding:
    """Synthesize the executive-summary lead Finding for a SOC reader.

    Headline branches on what the queue actually says:
    - Top entity is Assign with ``security_evidence`` primary domain →
      lead with the dominant SIEM/bad-bot signal and any second-line
      SIEM corroboration.
    - Top Assign with a movement primary → lead with volume + bot-share
      delta so the analyst sees the operative concentration.
    - Only Watch → "N to watch."
    - All Insufficient / All Close — analogous boilerplate.

    Body italicizes the queue-state clarification. Recommendation pulls
    from the top aggregated action's ``summary`` form (single source of
    truth — the inlined actions section uses the analyst-grade
    ``detail``). Caveat fires when ≥ 50% of fleet rule evaluations had
    missing inputs.
    """
    counts = triage_strip.get("counts", {})
    n_assign = counts.get("assign", 0)
    n_watch = counts.get("watch", 0)
    n_insufficient = counts.get("insufficient_data", 0)
    n_close = counts.get("close_as_expected", 0)

    top_entity_card = _top_assign_card(queue_rows, scorecards)
    noun = entity_type_label or "entity"
    plural = entity_type_label_plural or (
        noun if noun.endswith("s") else f"{noun}s"
    )

    # Pluralize noun on the predicated count (n_assign / n_watch / …),
    # matching the style ``scorecard_brief._actionable_summary`` uses for
    # its host-count phrasing.
    def _noun_form(count: int) -> str:
        return noun if count == 1 else plural

    if n_assign and top_entity_card:
        sc = top_entity_card["scorecard"]
        entity_display = top_entity_card["entity_display"]
        primary = sc.get("primary_domain") or ""
        if primary == "security_evidence":
            lead_clause = _security_lead_clause(sc)
        else:
            lead_clause = _movement_lead_clause(sc)
        share_clause = _traffic_share_clause(sc, scorecards, n_total)
        verb = "needs" if n_assign == 1 else "need"
        head = (
            f"{n_assign} of {n_total} {_noun_form(n_assign)} {verb} analyst "
            f"attention — start with {entity_display}"
        )
        if share_clause:
            head += (
                f" ({share_clause}; {lead_clause})"
                if lead_clause
                else f" ({share_clause})"
            )
        elif lead_clause:
            head += f" ({lead_clause})"
        headline = head
    elif n_watch:
        headline = f"{n_watch} of {n_total} {_noun_form(n_watch)} to watch"
    elif n_insufficient and not n_close:
        headline = (
            f"{n_insufficient} of {n_total} {_noun_form(n_insufficient)} "
            "cannot be judged from this report alone"
        )
    elif n_close == n_total and n_total > 0:
        headline = f"All {n_total} {_noun_form(n_total)} read clean"
    else:
        headline = f"{n_total} {_noun_form(n_total)} reviewed"

    body_parts: list[str] = []
    routing = _routing_clause(queue_rows, noun, plural)
    if routing:
        body_parts.append(routing)
    if n_insufficient:
        body_parts.append(
            f"{n_insufficient} cannot be judged from this report alone"
        )
    body = "; ".join(body_parts) + "." if body_parts else ""

    recommendation: str | None = None
    if actions:
        top = actions[0]
        short = (
            top.get("summary") or top.get("detail") or top.get("step") or ""
        ).rstrip(".")
        host_count = top.get("host_count") or 0
        if host_count and host_count < n_total:
            recommendation = (
                f"{short} (affects {host_count} {noun if host_count == 1 else plural})."
            )
        else:
            recommendation = f"{short}." if short else None

    caveat: str | None = None
    total_missing = sum(c.get("missing_input", 0) for c in coverage.values())
    total_rules = sum(sum(c.values()) for c in coverage.values())
    if total_rules and total_missing / total_rules >= 0.5:
        pct = 100 * total_missing / total_rules
        caveat = (
            f"Coverage is thin — {pct:.0f}% of rule evaluations had "
            "missing inputs. Real risk may be higher than the score implies."
        )

    return Finding(
        finding_id="actionable_summary",
        title=headline,
        headline=headline,
        body=body,
        recommendation=recommendation,
        caveat=caveat,
        priority=100,
    )


def _routing_clause(
    queue_rows: list[dict],
    noun: str,
    plural: str,
) -> str:
    """Build a deterministic SOC routing clause from queue verdicts.

    Returns something like ``"SOC investigate ASN 64500 now; monitor / enrich
    ASN 64600"`` — names the Assign entities (up to two) explicitly so the
    reader knows who to act on first, and groups Watch entities with the
    softer verb. Empty when the queue has no Assign or Watch entities.
    """
    assigns = [
        r for r in queue_rows if (r.get("verdict_state") or "") == "assign"
    ]
    watches = [
        r for r in queue_rows if (r.get("verdict_state") or "") == "watch"
    ]
    if not assigns and not watches:
        return ""

    def _names(rows: list[dict], limit: int = 2) -> str:
        labels = [
            (r.get("entity_display") or r.get("entity") or "") for r in rows
        ]
        labels = [lbl for lbl in labels if lbl]
        if not labels:
            return ""
        if len(labels) <= limit:
            return ", ".join(labels)
        return ", ".join(labels[:limit]) + f", +{len(labels) - limit} more"

    parts: list[str] = []
    if assigns:
        names = _names(assigns)
        verb_noun = noun if len(assigns) == 1 else plural
        parts.append(f"SOC investigate {names} now" if names else f"investigate {len(assigns)} {verb_noun} now")
    if watches:
        names = _names(watches)
        parts.append(f"monitor / enrich {names}" if names else f"monitor / enrich {len(watches)}")
    return "; ".join(parts)


def _security_lead_clause(sc: dict) -> str:
    """Tight clause naming the dominant security-evidence signal.

    Picks the highest-priority triggered security_evidence rule and
    appends a SIEM corroboration tag when a SIEM-named rule also fired.
    """
    triggered = [
        r
        for r in scorecards_mod.normalize_rule_results(sc)
        if r.get("status") == "triggered" and r.get("domain") == "security_evidence"
    ]
    if not triggered:
        return ""
    ordered = _sort_security_rules(triggered)
    lead_rule = ordered[0]
    name = lead_rule.get("name") or ""
    current = lead_rule.get("current")
    if name == "bad_bot_share_high" and isinstance(current, (int, float)):
        lead = f"bad-bot share {current:g}%"
    elif name == "siem_auth_fail_present" and isinstance(current, (int, float)):
        lead = f"{int(current)} SIEM auth failures"
    elif name == "siem_blocked_present" and isinstance(current, (int, float)):
        lead = f"{int(current)} SIEM blocked requests"
    else:
        lead = humanize_identifier(name).lower()

    siem_present = any(
        (r.get("name") or "").startswith("siem_")
        and r.get("name") != lead_rule.get("name")
        for r in triggered
    )
    if siem_present and not name.startswith("siem_"):
        return f"{lead}, SIEM evidence present"
    return lead


def _movement_lead_clause(sc: dict) -> str:
    """Lead clause for an Assign entity whose primary domain is movement.

    Pulls the volume_delta_high and bot_share_delta_high triggered rules
    when present and renders them as a "volume +X, bot share +Ypp" pair
    so the analyst sees the operative concentration without scanning
    rule rows.
    """
    rules = {
        r.get("name"): r
        for r in scorecards_mod.normalize_rule_results(sc)
        if r.get("status") == "triggered"
    }
    parts: list[str] = []
    vol = rules.get("volume_delta_high")
    if vol:
        supporting = vol.get("supporting_metrics") or {}
        pct = supporting.get("pct_change")
        absolute = supporting.get("absolute_delta")
        if isinstance(pct, (int, float)):
            parts.append(f"volume +{int(pct)}%")
        elif isinstance(absolute, (int, float)):
            parts.append(f"volume +{int(absolute)}")
    share = rules.get("bot_share_delta_high")
    if share:
        supporting = share.get("supporting_metrics") or {}
        pp = supporting.get("absolute_delta_points")
        if isinstance(pp, (int, float)):
            parts.append(f"bot share +{pp:.1f}pp")
    if not parts and rules:
        # Fall back to humanizing the highest-points triggered rule.
        top = max(rules.values(), key=lambda r: r.get("points") or 0)
        parts.append(humanize_identifier(top.get("name") or "").lower())
    return ", ".join(parts)
