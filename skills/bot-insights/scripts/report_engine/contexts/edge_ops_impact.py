"""Context preparer for the edge ops impact report.

The edge ops impact lens scores ranked entities (typically ``client_asn``,
``request_host``, or ``bot_class``) on the ``cache_busting`` and
``origin_impact`` domains — cache-miss rate / delta, query-string diversity,
origin p95 delta, and origin-cost contribution share — then optionally
enriches the report with path-grain candidates from a
``cache_origin_impact_report.v1`` artifact when present in the wrapper.

The wrapper's ``report_type: "edge_ops_impact"`` routes here. The packet's
``schema_version: "bot_scorecard_artifacts.v1"`` keeps its schema-registry
mapping to ``scorecard_brief`` for raw-artifact mode — ``edge_ops_impact``
is a wrapper-only report.
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
    humanize_entity_value,
    humanize_identifier,
    title_case_label,
)
from ..theme import DOMAIN_LABELS, DOMAIN_ORDER
from ._shared import (
    _aggregate_coverage,
    _feature_row,
    _matrix_cell_tone,
    _queue_rows,
    _scorecard_rollup,
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
REPORT_TYPE = "edge_ops_impact"
TEMPLATE = "reports/edge_ops_impact.html"

NOTE_ID_TO_SLOT = {
    "llm-interpretation": "executive_summary",
    "llm-operational": "operational_interpretation",
    "llm-finding-overrides": "finding_overrides",
}

PURPOSE = {
    "kicker": "Bot Insights — edge & origin cost",
    "measures": (
        "A cost-impact score for each ranked entity (ASN, host, or "
        "bot class) on a 0–100 scale. Higher scores reflect more "
        "triggered cache-busting and origin-impact signals — cache-miss "
        "rate / delta, query-string diversity, origin p95 delta, "
        "origin-cost contribution share."
    ),
    "score_legend": (
        "Higher score = more triggered edge/origin rules. "
        "Bands: escalate, monitor, observe."
    ),
    "cant_say": (
        "Origin cost is reported as a percentage share, not a billing "
        "figure. Missing inputs are reported as missing — they are "
        "not scored as zero cost."
    ),
}

# Edge-ops rules, ranked by how strongly they imply analyst action.
# Used to pick the lead clause when the cost-share lens cannot fire.
_EDGE_RULE_ORDER = (
    "origin_cost_contribution_high",
    "origin_p95_delta_high",
    "cache_miss_rate_high",
    "cache_miss_delta_high",
    "querystring_diversity_with_high_miss_rate",
    "querystring_diversity_high",
)


def assemble(artifacts: list[dict]) -> dict:
    """Reassemble a ``bot_report_input.v1`` wrapper's artifacts into the dict
    shape ``prepare()`` expects.

    Accepts both shapes the producer may emit:
    - Bundled: a single ``bot_scorecard_artifacts.v1`` packet that nests
      ``index`` + ``scorecards``.
    - Flat: separate ``bot_scorecard_index.v1`` and a list of
      ``bot_entity_scorecard.v1`` entries.

    Additionally extracts the first ``cache_origin_impact_report.v1``
    artifact (or None) into ``path_report``.
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
        raise ValueError(
            "edge_ops_impact wrapper missing bot_scorecard_index.v1 artifact"
        )

    if total_ranked is None:
        total_ranked = len(index.get("ranked_entities") or [])

    path_report = next(
        (
            a
            for a in artifacts
            if a.get("schema_version") == "cache_origin_impact_report.v1"
        ),
        None,
    )

    return {
        "schema_version": SCHEMA,
        "index": index,
        "scorecards": scorecards,
        "producer_limit": producer_limit,
        "result_row_count": len(scorecards),
        "result_truncated": result_truncated,
        "total_ranked_entities": total_ranked,
        "path_report": path_report,
    }


def prepare(artifact: dict) -> dict:
    index = artifact["index"]
    scorecards = artifact.get("scorecards") or []
    ranked_entities = index.get("ranked_entities") or []
    path_report = artifact.get("path_report")

    # Degraded fallback: wrapper carries the ranking but no scorecard
    # artifacts. Build minimal shadow cards from the index so the queue
    # table still renders, even though every per-rule view (edge evidence,
    # domain matrix, actions) lands empty.
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

    domain_matrix: dict
    if degraded:
        edge_cards: list[dict] = []
        scorecard_rollup: list[dict] = []
        domain_matrix = {"domains": [], "rows": []}
    else:
        edge_cards = _edge_evidence_cards(
            scorecards, verdicts_by_entity, rank_lookup, entity_type
        )
        scorecard_rollup = _scorecard_rollup(entities)
        domain_matrix = _domain_score_matrix(
            scorecards, rank_lookup, entity_type, coverage
        )

    # Build path candidates from the path-grain artifact (if present).
    path_candidates = _build_path_candidates(path_report)

    actionable = _actionable_summary(
        scorecards,
        queue_rows,
        triage_strip,
        actions,
        coverage,
        n_total,
        entity_type_label,
        path_candidates,
        entity_type_label_plural,
    )
    findings = [actionable]

    confidence_counts = Counter(sc.get("confidence") or "low" for sc in scorecards)
    confidence_reasons: set[str] = set()
    for sc in scorecards:
        confidence_reasons.update(sc.get("confidence_reasons") or [])

    headline_scope = cluster_label or scope_host or "fleet"
    headline = (
        f"Edge & Origin Cost — {headline_scope}, {entity_type_label} impact queue"
    )
    dek = (
        "Top entities ranked by triggered cache-busting and origin-impact "
        "signals for the current window."
    )

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
        "title": "Edge & Origin Cost",
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
        "edge_cards": edge_cards,
        "scorecard_rollup": scorecard_rollup,
        "domain_matrix": domain_matrix,
        "actions": _entity_actions(scorecards, entity_type),
        "aggregated_actions": actions,
        "coverage_rows": _coverage_rows(coverage),
        "path_candidates": path_candidates,
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
    """Pick the producer's entity_type axis. Edge producers commonly rank on
    ``client_asn`` or ``request_host``; fall back so an empty index still
    labels sensibly.
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
    avoids that ambiguity. AI-category slugs (e.g. ``ai_training``) get
    Title Case with acronym preservation. Other entity_types render as-is.
    """
    if not entity:
        return entity
    return humanize_entity_value(entity, entity_type)


def _coverage_rows(coverage: dict[str, dict[str, int]]) -> list[dict]:
    """Coverage rows for edge_ops_impact. Always lead with ``cache_busting``
    and ``origin_impact``, then any domain that contributed evaluations.
    """
    rows: list[dict] = []
    lead_domains = ["cache_busting", "origin_impact"]
    ordered_domains = lead_domains + [d for d in DOMAIN_ORDER if d not in lead_domains]
    for domain in ordered_domains:
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


def _edge_evidence_cards(
    scorecards: list[dict],
    verdicts_by_entity: dict[str, dict],
    rank_lookup: dict[str, int],
    entity_type: str,
) -> list[dict]:
    """Per-entity card for each Assign or Watch entity.

    Each card foregrounds the cache_busting and origin_impact domains —
    the rules whose triggers tell the analyst what to investigate — then
    lists adjacent triggered features below as supporting context.
    Closed-as-expected and Insufficient entities are omitted; the queue
    table covers them.
    """
    edge_domains = {"cache_busting", "origin_impact"}
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
        edge = [r for r in triggered if r.get("domain") in edge_domains]
        other = [r for r in triggered if r.get("domain") not in edge_domains]
        edge_features = [_feature_row(r) for r in _sort_edge_rules(edge)]
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
                "edge_features": edge_features,
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


def _sort_edge_rules(rules: list[dict]) -> list[dict]:
    priority = {name: i for i, name in enumerate(_EDGE_RULE_ORDER)}
    return sorted(
        rules,
        key=lambda r: (
            priority.get(r.get("name") or "", len(_EDGE_RULE_ORDER)),
            -(r.get("points") or 0),
            r.get("name") or "",
        ),
    )


def _domain_score_matrix(
    scorecards: list[dict],
    rank_lookup: dict[str, int],
    entity_type: str,
    coverage: dict[str, dict[str, int]],
) -> dict:
    """Entities × domains grid of per-cell point totals.

    Always includes the edge domains; also includes any other domain that
    actually scored.
    """
    active: set[str] = {"cache_busting", "origin_impact"}
    for sc in scorecards:
        for domain, value in (sc.get("domain_scores") or {}).items():
            try:
                if float(value) > 0:
                    active.add(domain)
            except (TypeError, ValueError):
                continue
    for domain in coverage:
        if coverage[domain].get("triggered", 0):
            active.add(domain)
    domains = [d for d in DOMAIN_ORDER if d in active]
    if not domains:
        domains = ["cache_busting", "origin_impact"]

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
    aggregated = _aggregate_actions(scorecards)
    out: list[dict] = []
    for action in aggregated:
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


# ---- path-grain helpers -----------------------------------------------------


def _path_primary_label(dimensions: dict) -> str:
    """Join dimension values with ' · ', leading with request_path_norm."""
    if not dimensions:
        return ""
    parts: list[str] = []
    path = dimensions.get("request_path_norm")
    if path is not None:
        parts.append(str(path))
    for key, value in sorted(dimensions.items()):
        if key != "request_path_norm" and value is not None:
            parts.append(str(value))
    return " · ".join(parts) if parts else ""


def _miss_share(cand: dict) -> float | None:
    """Extract cache_miss_pct from the candidate's current metrics."""
    try:
        val = cand.get("current", {}).get("cache_miss_pct")
        if val is None:
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _origin_share(cand: dict) -> float | None:
    """Extract origin pressure contribution share from current metrics."""
    current = cand.get("current") or {}
    for key in ("origin_pressure_contribution_pct", "origin_cost_contribution_pct"):
        val = current.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return None


def _path_evidence_line(cand: dict) -> str:
    """One short string summarizing the deltas for the path row footer."""
    deltas = cand.get("deltas") or {}
    parts: list[str] = []
    for key, value in sorted(deltas.items()):
        if value is None:
            continue
        try:
            f = float(value)
        except (TypeError, ValueError):
            continue
        # Render percentage-point-like fields as pp, ratio/pct fields as %
        if "pct" in key or "rate" in key or "share" in key:
            sign = "+" if f >= 0 else ""
            parts.append(f"{key} {sign}{f:.0f}pp")
        else:
            sign = "+" if f >= 0 else ""
            parts.append(f"{key} {sign}{f:.0f}%")
    return ", ".join(parts[:4])  # cap at 4 clauses to keep it readable


def _build_path_candidates(path_report: dict | None) -> list[dict]:
    """Build the path_candidates list from a cache_origin_impact_report.v1."""
    if path_report is None:
        return []
    raw_candidates = path_report.get("candidates") or []
    result: list[dict] = []
    for cand in raw_candidates:
        result.append(
            {
                "rank": cand.get("rank"),
                "dimensions": cand.get("entity") or {},
                "primary_label": _path_primary_label(cand.get("entity") or {}),
                "score": cand.get("candidate_score"),
                "band": cand.get("candidate_band"),
                "confidence": cand.get("confidence"),
                "current": cand.get("current") or {},
                "baseline": cand.get("baseline") or {},
                "deltas": cand.get("deltas") or {},
                "finding_types": cand.get("finding_types") or [],
                "miss_share_pct": _miss_share(cand),
                "origin_share_pct": _origin_share(cand),
                "evidence": _path_evidence_line(cand),
            }
        )
    return result


# ---- actionable summary and lead-clause helpers -----------------------------


def _cost_share_from_scorecard(sc: dict) -> float | None:
    """Extract origin_cost_contribution_pct from the triggered rule.

    Looks on the ``origin_cost_contribution_high`` rule's ``current``
    field, and falls back to ``supporting_metrics.cost_share_pct``.
    Returns None when the rule is absent or the value is not numeric.
    """
    for rule in scorecards_mod.normalize_rule_results(sc):
        if rule.get("name") != "origin_cost_contribution_high":
            continue
        current = rule.get("current")
        if isinstance(current, (int, float)):
            return float(current)
        supporting = rule.get("supporting_metrics") or {}
        val = supporting.get("cost_share_pct")
        if isinstance(val, (int, float)):
            return float(val)
    return None


def _edge_lead_clause(
    sc: dict,
    actionable_scorecards: list[dict],
    path_candidates: list[dict],
) -> str:
    """Lead clause for the executive-summary headline.

    Prefers cost-share when every actionable scorecard carries
    ``origin_cost_contribution_pct``. Falls back to the highest-priority
    triggered edge rule otherwise.

    When path candidates exist, appends a top-path clause when miss_share
    is available.
    """
    # Attempt cost-share lens.
    cost_shares: list[float] = []
    for asc in actionable_scorecards:
        val = _cost_share_from_scorecard(asc)
        if val is None:
            cost_shares = []
            break
        cost_shares.append(val)

    n_assign = len(actionable_scorecards)
    if cost_shares:
        total_pct = sum(cost_shares)
        lead = (
            f"top {n_assign} {'entity' if n_assign == 1 else 'entities'} "
            f"concentrate {total_pct:.0f}% of origin pressure"
        )
    else:
        lead = _rule_based_lead_clause(sc)

    # Append path clause when top path has a miss_share.
    if path_candidates:
        top = path_candidates[0]
        miss_share = top.get("miss_share_pct")
        primary_label = top.get("primary_label") or ""
        if miss_share is not None and primary_label:
            lead += (
                f"; top path {primary_label} carries {miss_share:.0f}% of cache misses"
            )

    return lead


def _rule_based_lead_clause(sc: dict) -> str:
    """Lead clause based on the highest-priority triggered edge rule."""
    triggered = [
        r
        for r in scorecards_mod.normalize_rule_results(sc)
        if r.get("status") == "triggered"
        and r.get("domain") in {"cache_busting", "origin_impact"}
    ]
    if not triggered:
        # Fallback: any triggered rule across all domains.
        triggered = [
            r
            for r in scorecards_mod.normalize_rule_results(sc)
            if r.get("status") == "triggered"
        ]
    if not triggered:
        return ""
    ordered = _sort_edge_rules(triggered)
    lead_rule = ordered[0]
    name = lead_rule.get("name") or ""
    current = lead_rule.get("current")
    supporting = lead_rule.get("supporting_metrics") or {}

    if name == "origin_cost_contribution_high" and isinstance(current, (int, float)):
        return f"origin cost contribution {current:g}%"
    if name == "origin_p95_delta_high":
        pct = supporting.get("pct_change")
        if isinstance(pct, (int, float)):
            return f"origin p95 latency +{int(pct)}%"
        return "origin p95 latency spike"
    if name == "cache_miss_rate_high" and isinstance(current, (int, float)):
        return f"cache-miss rate {current:g}%"
    if name == "cache_miss_delta_high":
        pct = supporting.get("pct_change")
        if isinstance(pct, (int, float)):
            return f"cache-miss rate +{int(pct)}%"
        return "cache-miss rate spike"
    if name == "querystring_diversity_with_high_miss_rate" and isinstance(
        current, (int, float)
    ):
        return f"query-string diversity {int(current)} unique QS with high miss rate"
    if name == "querystring_diversity_high" and isinstance(current, (int, float)):
        return f"query-string diversity {int(current)} unique QS"
    return humanize_identifier(name).lower()


def _actionable_summary(
    scorecards: list[dict],
    queue_rows: list[dict],
    triage_strip: dict,
    actions: list[dict],
    coverage: dict[str, dict[str, int]],
    n_total: int,
    entity_type_label: str,
    path_candidates: list[dict],
    entity_type_label_plural: str | None = None,
) -> Finding:
    """Synthesize the executive-summary lead Finding for an edge reader.

    Headline branches on what the queue actually says:
    - Top entity is Assign → lead with the cost-share clause when every
      actionable entity carries origin_cost_contribution_pct, otherwise
      fall back to the highest-priority triggered edge rule.
    - Only Watch → "N to watch."
    - All Insufficient / All Close — analogous boilerplate.
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

    def _noun_form(count: int) -> str:
        return noun if count == 1 else plural

    if n_assign and top_entity_card:
        sc = top_entity_card["scorecard"]
        entity_display = top_entity_card["entity_display"]

        # Collect all actionable (Assign) scorecards for cost-share check.
        assign_entities = {
            r.get("entity") for r in queue_rows if r.get("verdict_state") == "assign"
        }
        actionable_scs = [s for s in scorecards if s.get("entity") in assign_entities]

        lead_clause = _edge_lead_clause(sc, actionable_scs, path_candidates)
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
    if n_assign and n_watch:
        body_parts.append(f"{n_watch} to watch")
    if n_insufficient:
        body_parts.append(f"{n_insufficient} cannot be judged from this report alone")
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
