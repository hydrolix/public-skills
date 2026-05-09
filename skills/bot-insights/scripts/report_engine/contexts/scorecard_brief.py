"""Context preparer for `bot_scorecard_artifacts.v1` artifacts."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median

from .. import verdicts as verdicts_mod
from .. import volume_impact as vi
from ..findings import Finding, build_scorecard_brief_findings
from ..formatters import format_share_pct
from ..humanize import cluster_display, humanize_identifier
from ..theme import DOMAIN_LABELS, DOMAIN_ORDER

SCHEMA = "bot_scorecard_artifacts.v1"
REPORT_TYPE = "scorecard_brief"
TEMPLATE = "reports/scorecard_brief.html"

# Wrapper analyst-note routing. (report_type, note_id) -> slot name.
NOTE_ID_TO_SLOT = {
    "llm-interpretation": "executive_summary",
    "llm-operational": "operational_interpretation",
    "llm-finding-overrides": "finding_overrides",
}

PURPOSE = {
    "report_class_fleet": "Bot & Cache Health Scorecard — fleet review",
    "report_class_single": "Bot & Cache Health Scorecard — entity review",
    "measures": (
        "A health score for each request host on a 0–100 scale. Every host "
        "starts at 100 and loses points when mechanical signals — cache-miss "
        "rate, query-string churn, error rate, bot-share movement — cross "
        "thresholds. This window's scores are compared with the prior "
        "equivalent window."
    ),
    "score_legend": (
        "Higher is healthier (100 = clean); lower = more triggered rules. "
        "Bands: escalate 0–40, monitor 40–70, observe 70–100."
    ),
    "cant_say": (
        "Not a root-cause diagnosis or a malicious-traffic call. "
        "Missing inputs are reported as missing — they are not scored as safe."
    ),
}


def assemble(artifacts: list[dict]) -> dict:
    """Reassemble a `bot_report_input.v1` wrapper's artifacts into the dict
    shape `prepare()` expects.

    The wrapper sends `[<bot_entity_scorecard.v1>, <bot_scorecard_index.v1>]`
    as separate list entries. Raw artifacts (`bot_scorecard_artifacts.v1`)
    instead bundle them as `{"index": ..., "scorecards": [...]}`. We unify on
    the bundled shape since `prepare()` was written against it.
    """
    cards = [
        a for a in artifacts if a.get("schema_version") == "bot_entity_scorecard.v1"
    ]
    index = next(
        (a for a in artifacts if a.get("schema_version") == "bot_scorecard_index.v1"),
        None,
    )
    if not cards:
        raise ValueError(
            "scorecard_brief wrapper missing bot_entity_scorecard.v1 artifacts"
        )
    if index is None:
        raise ValueError(
            "scorecard_brief wrapper missing bot_scorecard_index.v1 artifact"
        )
    return {
        "schema_version": SCHEMA,
        "scorecards": cards,
        "index": index,
        # Mirror the producer fields prepare() reads from raw artifacts.
        "producer_limit": index.get("producer_limit"),
        "result_row_count": len(cards),
        "result_truncated": False,
        "total_ranked_entities": len(index.get("ranked_entities", [])),
    }


def prepare(artifact: dict) -> dict:
    index = artifact["index"]
    scorecards = artifact["scorecards"]
    scope = scorecards[0]["scope"]
    table_used = scorecards[0]["table_used"]

    n_total = len(scorecards)
    n_with_triggers = sum(
        1
        for sc in scorecards
        if any(r["status"] == "triggered" for r in sc["rule_results"])
    )
    n_clean = n_total - n_with_triggers
    n_moved = sum(1 for sc in scorecards if sc.get("score_delta_points", 0) != 0)

    band_counts = Counter(sc["band"] for sc in scorecards)
    for b in ("escalate", "monitor", "observe"):
        band_counts.setdefault(b, 0)

    confidence_counts = Counter(sc["confidence"] for sc in scorecards)
    domain_counts = Counter(sc["primary_domain"] for sc in scorecards)

    confidence_reasons: set[str] = set()
    for sc in scorecards:
        confidence_reasons.update(sc.get("confidence_reasons") or [])

    coverage = _aggregate_coverage(scorecards)
    coverage_rows = _coverage_rows(coverage)
    actions = _aggregate_actions(scorecards)

    scores = [sc["score"] for sc in scorecards]
    score_dist = Counter(scores)

    findings = build_scorecard_brief_findings(
        scorecards,
        n_total,
        n_with_triggers,
        n_clean,
        n_moved,
        domain_counts,
        coverage,
    )

    rank_lookup = {e["entity"]: e["rank"] for e in index["ranked_entities"]}
    ranked = sorted(scorecards, key=lambda s: rank_lookup.get(s["entity"], 999))
    entities = [_entity_row(sc, rank_lookup) for sc in ranked]

    # Per-host verdict — same 4-state classifier the entity-review uses.
    # The fleet aggregates these into the triage strip; the queue table
    # sorts by verdict so the work-to-do reads top-down.
    verdicts_by_entity: dict[str, dict] = {}
    for sc in scorecards:
        rc = _rule_counts(sc)
        verdict = verdicts_mod.classify(sc["band"], rc)
        verdicts_by_entity[sc["entity"]] = verdict
    for e in entities:
        v = verdicts_by_entity.get(e["entity"])
        e["verdict"] = v
        e["verdict_state"] = v["state"] if v else "watch"
        e["verdict_label"] = v["label"] if v else "Watch"
        e["verdict_tone"] = v["tone"] if v else "monitor"

    triage_strip = _triage_strip(verdicts_by_entity, n_total)
    shared_signal = _shared_signal(scorecards, n_total)
    fleet_volume_impact = vi.project_fleet(scorecards)
    fleet_coverage_detail = _fleet_coverage_detail(scorecards, n_total)

    # Synthesize the actionable summary AFTER triage/shared-signal/actions
    # are computed, then prepend so the executive_summary macro lifts it as
    # the lead paragraph.
    actionable = _actionable_summary(
        triage_strip,
        shared_signal,
        actions,
        n_total,
        coverage,
    )
    findings = [actionable, *findings]

    queue_rows = _queue_rows(entities)
    entity_rows = _group_entities(entities)
    lowest_host = _lowest_host_callout(queue_rows)

    is_single = n_total == 1
    fleet_total = artifact.get("total_ranked_entities") or len(
        index.get("ranked_entities") or []
    )
    # When the wrapper carries a single selected scorecard but the index
    # describes a larger fleet, frame the report as "1 of N" so the reader
    # knows this is a selected entity, not the whole fleet.
    is_selected_from_fleet = is_single and fleet_total > 1
    cluster_label = cluster_display(scope["cluster"])
    if is_single:
        kicker = PURPOSE["report_class_single"]
        if is_selected_from_fleet:
            headline = (
                f"{cluster_label} — {scorecards[0]['entity']} "
                f"(1 of {fleet_total} hosts)"
            )
        else:
            headline = f"{cluster_label} — {scorecards[0]['entity']}"
    else:
        kicker = PURPOSE["report_class_fleet"]
        headline = f"{cluster_label} — {n_total} request hosts reviewed"

    dek = _compute_dek(
        n_total,
        n_with_triggers,
        n_moved,
        is_single,
        lowest=min(scores),
        fleet_total=fleet_total if is_selected_from_fleet else None,
    )

    return {
        "title": "Bot Scorecard Brief",
        "kicker": kicker,
        "headline": headline,
        "dek": dek,
        # Suppress base.html's above-the-fold purpose strip; orientation moves
        # behind a disclosure inside content. Same pattern as entity-review.
        "purpose": None,
        "orientation": {
            "measures": PURPOSE["measures"],
            "score_legend": PURPOSE["score_legend"],
            "cant_say": PURPOSE["cant_say"],
        },
        "scope": {
            "cluster": scope["cluster"],
            "database": scope["database"],
            "table_used": table_used,
        },
        "windows": {
            "current": index["current_window"],
            "baseline": index["baseline_windows"][0],
        },
        "kpis": {
            "n_total": n_total,
            "n_with_triggers": n_with_triggers,
            "n_clean": n_clean,
            "n_moved": n_moved,
            "bands": dict(band_counts),
        },
        "score_summary": {
            "lowest": min(scores),
            "median": int(median(scores)),
            "highest": max(scores),
            "distribution": sorted(score_dist.items()),
            "bands": dict(band_counts),
            "lowest_delta_pct": _lowest_delta_pct(scorecards),
            "scores": scores,
        },
        "findings": findings,
        "triage_strip": triage_strip,
        "lowest_host": lowest_host,
        "shared_signal": shared_signal,
        "fleet_volume_impact": fleet_volume_impact,
        "fleet_coverage_detail": fleet_coverage_detail,
        "queue_rows": queue_rows,
        "coverage_rows": coverage_rows,
        "confidence": {
            "counts": dict(confidence_counts),
            "reasons": sorted(confidence_reasons),
        },
        "entities": entities,
        "entity_rows": entity_rows,
        "actions": actions,
        "method": {
            "schema_version": artifact.get("schema_version"),
            "comparison_type": index.get("comparison_type"),
            "producer_limit": index.get("producer_limit"),
            "result_row_count": artifact.get("result_row_count"),
            "result_truncated": artifact.get("result_truncated"),
            "interpretation_constraints": index.get("interpretation_constraints") or [],
        },
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# ---- helpers ----------------------------------------------------------------


def _lowest_host_callout(queue_rows: list[dict]) -> dict | None:
    """Pick the lowest-scoring host from already-sorted queue_rows.

    Returns the entity name, score, and verdict pill data so the brief
    landscape section can render a "Lowest scoring host: X · Score Y ·
    <pill>" callout without forcing the reader through a gauge.
    """
    if not queue_rows:
        return None
    target = min(queue_rows, key=lambda r: r.get("score", 100))
    return {
        "entity": target.get("entity"),
        "score": target.get("score"),
        "verdict_label": target.get("verdict_label"),
        "verdict_tone": target.get("verdict_tone"),
        "verdict_state": target.get("verdict_state"),
    }


def _lowest_delta_pct(scorecards: list[dict]) -> float:
    """Percent change of the lowest-current-score host vs its baseline.

    Uses the per-scorecard `baseline_score` field. Anchors the gauge: the
    big number is the lowest current score; the delta is that same host's
    change vs its prior equivalent window.
    """
    lowest = min(scorecards, key=lambda s: s["score"])
    baseline = lowest.get("baseline_score")
    current = lowest["score"]
    if baseline is None or baseline == 0:
        return 0.0
    return (current - baseline) / baseline * 100


def _compute_dek(
    n_total: int,
    n_with_triggers: int,
    n_moved: int,
    is_single: bool,
    lowest: int,
    fleet_total: int | None = None,
) -> str:
    """One-sentence outcome summary, deterministic from KPIs."""
    if is_single:
        # Single-entity view: focus on this host's score and movement.
        movement = (
            "no movement vs baseline" if n_moved == 0 else "score moved vs baseline"
        )
        if fleet_total:
            return (
                f"Selected entity from {fleet_total}-host fleet review. "
                f"Score {lowest}; {movement}."
            )
        return f"Score {lowest}; {movement}."

    # Fleet view: focus on triggered/clean split + movement count.
    if n_with_triggers == 0:
        triggers_clause = f"All {n_total} hosts produced no triggered rules"
    elif n_with_triggers == n_total:
        triggers_clause = f"All {n_total} hosts triggered at least one rule"
    else:
        triggers_clause = (
            f"{n_with_triggers} of {n_total} hosts triggered at least one rule"
        )

    movement_clause = (
        "no host scores moved versus baseline"
        if n_moved == 0
        else f"{n_moved} score{'s' if n_moved != 1 else ''} moved versus baseline"
    )
    return f"{triggers_clause}; {movement_clause}."


def _aggregate_coverage(scorecards: list[dict]) -> dict[str, dict[str, int]]:
    coverage: dict[str, Counter] = defaultdict(Counter)
    for sc in scorecards:
        for rule in sc["rule_results"]:
            coverage[rule["domain"]][rule["status"]] += 1
    return {d: dict(c) for d, c in coverage.items()}


def _coverage_rows(coverage: dict[str, dict[str, int]]) -> list[dict]:
    rows = []
    for domain in DOMAIN_ORDER:
        counts = coverage.get(domain, {})
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


def _normalize_step(step: object) -> dict[str, str]:
    """Adapt a recommended-next-step entry to ``{summary, detail}``.

    Current scorecard producers emit dicts with both fields. Older
    artifacts emit plain strings; promote the string into the structured
    shape (summary = first sentence) so the rest of the pipeline only
    deals with one shape.
    """
    if isinstance(step, dict):
        summary = (step.get("summary") or step.get("detail") or "").strip()
        detail = (step.get("detail") or step.get("summary") or "").strip()
        return {"summary": summary, "detail": detail}
    text = str(step).strip()
    head = text.split(".")[0]
    summary = head + ("." if head and head != text else "")
    return {"summary": summary or text, "detail": text}


def _aggregate_actions(scorecards: list[dict]) -> list[dict]:
    """Group recommended next steps across the fleet by their detail text.

    The detail string is the stable identity — different summaries that
    share a detail collapse to one entry. Each entry carries both grades
    so downstream views can pick the lens they need (executive summary
    pulls the short summary; the actions section renders the detail).
    """
    by_detail: dict[str, dict] = {}
    order: list[str] = []
    for sc in scorecards:
        for raw in sc.get("recommended_next_steps") or []:
            normalized = _normalize_step(raw)
            detail = normalized["detail"]
            if not detail:
                continue
            entry = by_detail.get(detail)
            if entry is None:
                entry = {
                    "summary": normalized["summary"],
                    "detail": detail,
                    "hosts": [],
                }
                by_detail[detail] = entry
                order.append(detail)
            entry["hosts"].append(sc["entity"])
    ordered = sorted(
        (by_detail[d] for d in order),
        key=lambda e: -len(e["hosts"]),
    )
    return [
        {
            "summary": e["summary"],
            "detail": e["detail"],
            # Keep ``step`` as an alias of detail for backwards compat with
            # any consumer that still reads the old field name.
            "step": e["detail"],
            "host_count": len(e["hosts"]),
            "preview": ", ".join(e["hosts"][:3]),
            "extra": max(0, len(e["hosts"]) - 3),
        }
        for e in ordered
    ]


_GROUP_THRESHOLD = 5


def _entity_signature(e: dict) -> tuple:
    """Identity tuple for collapsing visually identical rows.

    Excludes ``evidence_top`` since that string typically embeds a
    per-host numeric (e.g. cache-miss percentage), which would defeat
    grouping. The group row surfaces variance in the evidence cell.
    """
    return (
        e["score"],
        e["delta"],
        e["primary_domain"],
        e["band"],
        e["confidence"],
    )


def _group_entities(entities: list[dict]) -> list[dict]:
    """Collapse contiguous runs of identical entity rows into group rows.

    Runs of >= _GROUP_THRESHOLD identical rows render as a single summary
    row with the host list available behind a disclosure. Shorter runs
    render as individual rows (current behavior).
    """
    rows: list[dict] = []
    i = 0
    while i < len(entities):
        sig = _entity_signature(entities[i])
        j = i
        while j < len(entities) and _entity_signature(entities[j]) == sig:
            j += 1
        run = entities[i:j]
        if len(run) >= _GROUP_THRESHOLD:
            evidence_values = {e["evidence_top"] for e in run}
            rows.append(
                {
                    "kind": "group",
                    "count": len(run),
                    "first_rank": run[0]["rank"],
                    "last_rank": run[-1]["rank"],
                    "hosts": [e["entity"] for e in run],
                    "representative": run[0],
                    "evidence_varies": len(evidence_values) > 1,
                }
            )
        else:
            for e in run:
                rows.append({"kind": "single", "entity": e})
        i = j
    return rows


def _actionable_summary(
    triage_strip: dict,
    shared_signal: dict | None,
    actions: list[dict],
    n_total: int,
    coverage: dict[str, dict[str, int]],
) -> Finding:
    """Synthesize the top-of-summary actionable take.

    Reads from already-computed deterministic projections — triage strip,
    shared signal, aggregated actions, fleet coverage. Returns a Finding
    that the executive_summary macro renders as the lead paragraph.

    Three slots in the body, each only emitted when meaningful:
    1. State of the queue (X need attention now / Y to watch / Z insufficient).
    2. Recommended action — top aggregated action with host count, framed
       as one-issue-not-N when a shared signal is present.
    3. Coverage caveat when ≥ 50% of fleet rule evaluations were unscored.
    """
    counts = triage_strip.get("counts", {})
    n_assign = counts.get("assign", 0)
    n_watch = counts.get("watch", 0)
    n_insufficient = counts.get("insufficient_data", 0)
    n_close = counts.get("close_as_expected", 0)

    # Headline — what's the queue state in plain language.
    if n_assign:
        headline = (
            f"{n_assign} of {n_total} host{'s' if n_assign != 1 else ''} "
            "need attention now"
        )
    elif shared_signal:
        headline = (
            f"{shared_signal['host_count']} of {n_total} hosts share "
            f"{shared_signal['rule_label']}"
        )
        if shared_signal.get("traffic_share_pct") is not None:
            headline += (
                f" (covering {format_share_pct(shared_signal['traffic_share_pct'])}"
                " of fleet requests)"
            )
        headline += f" — investigate as one issue, not {shared_signal['host_count']}"
    elif n_watch:
        headline = f"{n_watch} of {n_total} host{'s' if n_watch != 1 else ''} to watch"
    elif n_insufficient and not n_close:
        headline = (
            f"{n_insufficient} of {n_total} host{'s' if n_insufficient != 1 else ''} "
            "cannot be judged from this report alone"
        )
    elif n_close == n_total and n_total > 0:
        headline = f"All {n_total} hosts read clean"
    else:
        headline = f"{n_total} host{'s' if n_total != 1 else ''} reviewed"

    # Body — state sentence covering whatever the headline didn't.
    state_bits: list[str] = []
    if n_assign and shared_signal:
        state_bits.append(
            f"{shared_signal['host_count']} of {n_total} share "
            f"{shared_signal['rule_label']}"
        )
    if n_watch and not headline.startswith(f"{n_watch} of"):
        state_bits.append(f"{n_watch} to watch")
    if n_insufficient and not headline.startswith(f"{n_insufficient} of"):
        state_bits.append(f"{n_insufficient} cannot be judged from this report alone")
    body = "; ".join(state_bits) + "." if state_bits else ""

    # Recommended action — pulled from aggregated next-steps. The executive
    # summary uses the producer's short ``summary`` form; the analyst-grade
    # ``detail`` form lives in the "Recommended next steps" section below.
    # When the action affects exactly the shared-signal host count, frame it
    # as one-cause rather than N independent investigations.
    recommendation: str | None = None
    if actions:
        top = actions[0]
        short = (
            top.get("summary") or top.get("detail") or top.get("step") or ""
        ).rstrip(".")
        host_count = top.get("host_count") or 0
        if shared_signal and host_count == shared_signal["host_count"]:
            recommendation = f"{short} — investigate as one cause, not {host_count}."
        elif host_count and host_count < n_total:
            recommendation = (
                f"{short} (affects {host_count} host{'s' if host_count != 1 else ''})."
            )
        else:
            recommendation = f"{short}."

    # Coverage caveat — kept structurally separate so the action isn't drowned.
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
        priority=100,  # always lead
    )


def _rule_counts(sc: dict) -> dict:
    rule_results = sc.get("rule_results") or []
    triggered = sum(1 for r in rule_results if r.get("status") == "triggered")
    below = sum(1 for r in rule_results if r.get("status") == "evaluated_zero")
    missing = sum(1 for r in rule_results if r.get("status") == "missing_input")
    return {
        "triggered": triggered,
        "below_threshold": below,
        "missing_input": missing,
        "total": len(rule_results),
    }


def _triage_strip(verdicts_by_entity: dict[str, dict], n_total: int) -> dict:
    """Aggregate per-host verdicts into the triage strip.

    Returns counts for each state in canonical order, plus a one-line
    rationale of the fleet split. The strip is the new hero: it answers
    "how many hosts need work" before the reader sees any score chrome.
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
    rationale_parts = []
    if n_assign:
        rationale_parts.append(
            f"{n_assign} host{'s' if n_assign != 1 else ''} need attention"
        )
    if n_watch:
        rationale_parts.append(f"{n_watch} to watch")
    if n_insufficient:
        rationale_parts.append(
            f"{n_insufficient} cannot be judged from this report alone"
        )
    if not rationale_parts and n_close:
        rationale_parts.append(
            f"all {n_close} host{'s' if n_close != 1 else ''} read clean"
        )
    if rationale_parts:
        rationale = "; ".join(rationale_parts) + f" (out of {n_total})."
    else:
        rationale = ""

    return {
        "pills": pills,
        "rationale": rationale,
        "counts": state_counts,
    }


def _shared_signal(scorecards: list[dict], n_total: int) -> dict | None:
    """Surface the dominant triggered rule when ≥ 50% of the fleet shares it.

    A shared signal more often points to a single fleet-wide cause than
    to N independent occurrences. Promoting it to the hero strip saves
    the reader from having to derive that pattern from the findings list.

    When request-volume data is available, also computes
    ``traffic_share_pct`` — the share of fleet requests carried by the
    affected hosts — so the headline can lead with traffic weight rather
    than a raw host count. ``None`` when any input is missing so the
    template can fall back to count-only framing.
    """
    if n_total < 2:
        return None
    triggered_counts: Counter = Counter()
    triggered_hosts: dict[str, set[str]] = defaultdict(set)
    for sc in scorecards:
        for r in sc.get("rule_results") or []:
            if r.get("status") == "triggered":
                name = r.get("name") or ""
                triggered_counts[name] += 1
                triggered_hosts[name].add(sc.get("entity") or "")
    if not triggered_counts:
        return None
    name, count = triggered_counts.most_common(1)[0]
    if count / n_total < 0.5:
        return None

    affected = triggered_hosts[name]
    fleet_requests = 0.0
    affected_requests = 0.0
    have_any_volume = False
    have_full_volume = True
    for sc in scorecards:
        metrics = sc.get("entity_metrics") or {}
        cur = metrics.get("current_requests")
        if cur is None:
            have_full_volume = False
            continue
        have_any_volume = True
        fleet_requests += float(cur)
        if (sc.get("entity") or "") in affected:
            affected_requests += float(cur)
    traffic_share_pct: float | None = None
    if have_any_volume and have_full_volume and fleet_requests > 0:
        traffic_share_pct = affected_requests / fleet_requests * 100.0

    return {
        "rule_name": name,
        "rule_label": humanize_identifier(name),
        "host_count": count,
        "fleet_total": n_total,
        "traffic_share_pct": traffic_share_pct,
        "headline": (
            f"{count} of {n_total} hosts share {humanize_identifier(name)} — "
            "investigate as one issue, not "
            f"{count}."
        ),
    }


def _fleet_coverage_detail(scorecards: list[dict], n_total: int) -> dict | None:
    """Group rules that are missing inputs across the fleet by domain.

    A rule that is unscored on most or all hosts surfaces as a coverage
    gap that needs to be fixed at the producer / data source, not at
    the host. Director sees "rule X has missing inputs on 18 of 20 hosts"
    not just per-host counts.
    """
    by_rule: dict[str, dict] = {}
    for sc in scorecards:
        for r in sc.get("rule_results") or []:
            if r.get("status") != "missing_input":
                continue
            name = r.get("name") or ""
            entry = by_rule.setdefault(
                name,
                {
                    "name": name,
                    "domain": r.get("domain") or "other",
                    "missing_inputs": tuple(r.get("missing_inputs") or []),
                    "host_count": 0,
                },
            )
            entry["host_count"] += 1
    if not by_rule:
        return None
    grouped: dict[str, list[dict]] = {}
    for entry in by_rule.values():
        grouped.setdefault(entry["domain"], []).append(entry)

    return {
        "n_total_hosts": n_total,
        "groups": [
            {
                "domain": d,
                "domain_label": DOMAIN_LABELS.get(d, d),
                "rules": sorted(rules, key=lambda r: (-r["host_count"], r["name"])),
            }
            for d, rules in sorted(grouped.items())
        ],
    }


_QUEUE_ORDER = {state: i for i, state in enumerate(verdicts_mod.STATE_ORDER)}


def _queue_rows(entities: list[dict]) -> list[dict]:
    """Sort entities by triage state first, then by score (lowest first),
    then by producer rank as a tiebreaker. Returns the same shape as
    individual entity rows but ordered for action.
    """
    return sorted(
        entities,
        key=lambda e: (
            _QUEUE_ORDER.get(e.get("verdict_state", "watch"), 99),
            e.get("score", 100),
            e.get("rank", 999) if isinstance(e.get("rank"), int) else 999,
        ),
    )


def _entity_row(sc: dict, rank_lookup: dict[str, int]) -> dict:
    triggered_rules = [
        r["name"] for r in sc["rule_results"] if r["status"] == "triggered"
    ]
    evidence = sc.get("evidence_summary") or []
    domain_label = DOMAIN_LABELS.get(sc["primary_domain"], sc["primary_domain"])
    delta = sc.get("score_delta_points", 0)
    return {
        "rank": rank_lookup.get(sc["entity"], "—"),
        "entity": sc["entity"],
        "score": sc["score"],
        "delta": delta,
        "primary_domain": sc["primary_domain"],
        "primary_domain_label": domain_label,
        "band": sc["band"],
        "confidence": sc["confidence"],
        "evidence_top": evidence[0] if evidence else "",
        "triggered_rules": triggered_rules,
    }
