"""Context preparer for the crawler governance report.

The crawler governance lens scores ranked entities (typically
``ai_category``, ``bot_class``, or ``request_host``) on the
``crawler_governance`` domain — good-bot 429 / error rates, AI-crawler
growth, and governance-surface failures — plus rate-delta context when
the producer ranked on a crawler-specific population.

The wrapper's ``report_type: "crawler_governance"`` routes here. The
packet's ``schema_version: "bot_scorecard_artifacts.v1"`` keeps its
schema-registry mapping to ``scorecard_brief`` for raw-artifact mode —
``crawler_governance`` is a wrapper-only report.
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
REPORT_TYPE = "crawler_governance"
TEMPLATE = "reports/crawler_governance.html"

NOTE_ID_TO_SLOT = {
    "llm-interpretation": "executive_summary",
    "llm-operational": "operational_interpretation",
    "llm-finding-overrides": "finding_overrides",
}

PURPOSE = {
    "kicker": "Bot Insights — crawler governance",
    "measures": (
        "A health score for each ranked crawler entity (AI category, bot "
        "class, or request host) on a 0–100 scale. Higher scores reflect "
        "more triggered crawler-governance signals — good-bot 429 / error "
        "rate, AI-crawler growth, governance surface failures — plus rate "
        "delta context when the rowset population is crawler-specific."
    ),
    "score_legend": (
        "Higher score = more triggered crawler-governance rules. "
        "Bands: escalate, monitor, observe."
    ),
    "cant_say": (
        "Not a confirmed-malicious-crawler call. Missing inputs are "
        "reported as missing — they are not scored as safe."
    ),
}

# Crawler-governance rules, ranked by how strongly they imply analyst
# action. Used to pick the lead clause when the top entity's primary
# domain is ``crawler_governance``.
_CRAWLER_RULE_ORDER = (
    "policy_surface_failure_present",
    "good_bot_429_present",
    "good_bot_error_rate_high",
    "ai_crawler_growth_high",
    "rate_429_delta_high",
    "rate_5xx_delta_high",
)


def assemble(artifacts: list[dict]) -> dict:
    """Reassemble a ``bot_report_input.v1`` wrapper's artifacts into the dict
    shape ``prepare()`` expects.

    Accepts both shapes the producer may emit:
    - Bundled: a single ``bot_scorecard_artifacts.v1`` packet that nests
      ``index`` + ``scorecards``.
    - Flat: separate ``bot_scorecard_index.v1`` and a list of
      ``bot_entity_scorecard.v1`` entries.
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
            "crawler_governance wrapper missing bot_scorecard_index.v1 artifact"
        )

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
    # table still renders, even though every per-rule view (crawler
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

    domain_matrix: dict
    if degraded:
        crawler_cards: list[dict] = []
        scorecard_rollup: list[dict] = []
        domain_matrix = {"domains": [], "rows": []}
    else:
        crawler_cards = _crawler_evidence_cards(
            scorecards, verdicts_by_entity, rank_lookup, entity_type
        )
        scorecard_rollup = _scorecard_rollup(entities)
        domain_matrix = _domain_score_matrix(
            scorecards, rank_lookup, entity_type, coverage
        )

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

    headline_scope = cluster_label or scope_host or "fleet"
    headline = (
        f"Crawler Governance — {headline_scope}, {entity_type_label} health queue"
    )
    dek = (
        "Top crawler entities ranked by triggered governance signals for "
        "the current window."
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
        "title": "Crawler Governance",
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
        "crawler_cards": crawler_cards,
        "scorecard_rollup": scorecard_rollup,
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
    """Pick the producer's entity_type axis. Crawler producers commonly
    rank on ``ai_category`` or ``bot_class``; fall back to ``request_host``
    so an empty index still labels sensibly.
    """
    for entry in ranked_entities:
        et = entry.get("entity_type")
        if et:
            return et
    for sc in scorecards:
        et = sc.get("entity_type")
        if et:
            return et
    return "request_host"


def _entity_display(entity: str, entity_type: str) -> str:
    """Reader-facing rendering of an entity identifier.

    For ASNs the bare number reads as a domain name; prepending the noun
    avoids that ambiguity. AI-category slugs (e.g. ``ai_training``) get
    Title Case with acronym preservation. Other entity_types render
    as-is — the identifier is already self-evident in the column.
    """
    if not entity:
        return entity
    return humanize_entity_value(entity, entity_type)


def _coverage_rows(coverage: dict[str, dict[str, int]]) -> list[dict]:
    """Coverage rows for crawler. Always lead with crawler_governance,
    then any domain that contributed evaluations (triggered, evaluated_zero,
    or missing inputs). Keeps the spotlight on crawler while still
    surfacing secondary-domain context (movement, cache_busting) when the
    producer ranked on ``request_host``.
    """
    rows: list[dict] = []
    ordered_domains = ["crawler_governance"] + [
        d for d in DOMAIN_ORDER if d != "crawler_governance"
    ]
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


def _crawler_evidence_cards(
    scorecards: list[dict],
    verdicts_by_entity: dict[str, dict],
    rank_lookup: dict[str, int],
    entity_type: str,
) -> list[dict]:
    """Per-entity card for each Assign or Watch entity.

    Each card foregrounds the crawler_governance domain — the rules
    whose triggers tell the analyst what to investigate first — then
    lists adjacent triggered features (movement, cache_busting, etc.)
    below as supporting context. Closed-as-expected and Insufficient
    entities are omitted; the queue table covers them.
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
        crawler = [r for r in triggered if r.get("domain") == "crawler_governance"]
        other = [r for r in triggered if r.get("domain") != "crawler_governance"]
        crawler_features = [_feature_row(r) for r in _sort_crawler_rules(crawler)]
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
                "crawler_features": crawler_features,
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


def _sort_crawler_rules(rules: list[dict]) -> list[dict]:
    priority = {name: i for i, name in enumerate(_CRAWLER_RULE_ORDER)}
    return sorted(
        rules,
        key=lambda r: (
            priority.get(r.get("name") or "", len(_CRAWLER_RULE_ORDER)),
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

    Filters the column set to domains that any entity actually scored
    on, plus crawler_governance regardless. Keeps the matrix dense for
    crawler reports where most domains are zero.
    """
    active: set[str] = {"crawler_governance"}
    for sc in scorecards:
        for domain, value in (sc.get("domain_scores") or {}).items():
            try:
                if float(value) > 0:
                    active.add(domain)
            except (TypeError, ValueError):
                continue
    for domain in coverage:
        if any(coverage[domain].get(s, 0) for s in ("triggered",)):
            active.add(domain)
    domains = [d for d in DOMAIN_ORDER if d in active]
    if not domains:
        domains = ["crawler_governance"]

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
    """Synthesize the executive-summary lead Finding for a crawler reader.

    Headline branches on what the queue actually says:
    - Top entity is Assign with ``crawler_governance`` primary domain →
      lead with the dominant crawler-governance signal and any second-line
      crawler corroboration.
    - Top Assign with another primary (movement, etc.) → lead with the
      SOC-style highest-points triggered rule pattern.
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
        primary = sc.get("primary_domain") or ""
        if primary == "crawler_governance":
            lead_clause = _crawler_lead_clause(sc)
        else:
            lead_clause = _fallback_lead_clause(sc)
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


def _crawler_lead_clause(sc: dict) -> str:
    """Tight clause naming the dominant crawler-governance signal.

    Picks the highest-priority triggered crawler_governance rule and
    formats its evidence into a compact phrase.
    """
    triggered = [
        r
        for r in scorecards_mod.normalize_rule_results(sc)
        if r.get("status") == "triggered" and r.get("domain") == "crawler_governance"
    ]
    if not triggered:
        return ""
    ordered = _sort_crawler_rules(triggered)
    lead_rule = ordered[0]
    name = lead_rule.get("name") or ""
    current = lead_rule.get("current")
    supporting = lead_rule.get("supporting_metrics") or {}
    if name == "policy_surface_failure_present" and isinstance(current, (int, float)):
        lead = f"{int(current)} governance-surface failures"
    elif name == "good_bot_429_present" and isinstance(current, (int, float)):
        lead = f"{int(current)} good-bot 429 responses"
    elif name == "good_bot_error_rate_high" and isinstance(current, (int, float)):
        lead = f"good-bot error rate {current:g}%"
    elif name == "ai_crawler_growth_high":
        pct = supporting.get("pct_change")
        if isinstance(pct, (int, float)):
            lead = f"AI crawler volume +{int(pct)}%"
        else:
            lead = "AI crawler growth"
    elif name == "rate_429_delta_high":
        pct = supporting.get("pct_change")
        if isinstance(pct, (int, float)):
            lead = f"429 rate +{int(pct)}%"
        else:
            lead = "429 rate spike"
    elif name == "rate_5xx_delta_high":
        pct = supporting.get("pct_change")
        if isinstance(pct, (int, float)):
            lead = f"5xx rate +{int(pct)}%"
        else:
            lead = "5xx rate spike"
    else:
        lead = humanize_identifier(name).lower()
    return lead


def _fallback_lead_clause(sc: dict) -> str:
    """Lead clause when the top entity's primary domain is not crawler.

    Picks the highest-points triggered rule across all domains and
    humanizes its name. Mirrors the SOC ``_movement_lead_clause`` shape
    without locking onto specific feature names.
    """
    triggered = [
        r
        for r in scorecards_mod.normalize_rule_results(sc)
        if r.get("status") == "triggered"
    ]
    if not triggered:
        return ""
    top = max(triggered, key=lambda r: r.get("points") or 0)
    return humanize_identifier(top.get("name") or "").lower()
