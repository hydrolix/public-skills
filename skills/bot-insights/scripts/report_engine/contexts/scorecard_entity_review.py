"""Context preparer for single-entity scorecard reviews.

Renders one ``bot_entity_scorecard.v1`` with the host's own evidence as the
centerpiece — triggered rules, observed values vs thresholds, recommended
next steps. Intentionally drops fleet-shape sections (findings band, score
histogram, ranked-hosts table) that read as filler at N=1.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone

from .. import scorecards as scorecards_mod
from .. import verdicts as verdicts_mod
from .. import volume_impact as vi
from ..findings import Finding
from ..humanize import cluster_display, humanize_identifier
from ..theme import DOMAIN_LABELS

SCHEMA = "bot_entity_scorecard.v1"
REPORT_TYPE = "scorecard_entity_review"
TEMPLATE = "reports/scorecard_entity_review.html"

# Reuse the slot names the wrapper producer emits for fleet reports —
# a singleton wrapper still carries `executive_summary` / `operational`
# notes against its single host.
NOTE_ID_TO_SLOT = {
    "llm-interpretation": "executive_summary",
    "llm-operational": "operational_interpretation",
}

PURPOSE = {
    "kicker": "Bot & Cache Health Scorecard — entity review",
    "measures": (
        "A health score for one request host on a 0–100 scale. The host "
        "starts at 100 and loses points when mechanical signals — cache-miss "
        "rate, query-string churn, error rate, bot-share movement — cross "
        "thresholds. The current window is compared with the prior "
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


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def post_prepare(ctx: dict) -> None:
    """Suppress structurally-redundant analyst notes after note merge.

    Called by the renderer after ``notes_by_slot`` has been injected. Drops
    the executive_summary note when its non-structural content is dominated
    by tokens already present in the deterministic finding body — saves the
    reader from reading the same fact twice.
    """
    notes = ctx.get("notes_by_slot") or {}
    findings = ctx.get("deterministic_findings") or []
    if not findings:
        return
    body = findings[0].get("body") or ""
    finding_tokens = _tokens(body)

    note = notes.get("executive_summary")
    if note and _is_redundant_note(note.get("text", ""), finding_tokens):
        notes = dict(notes)
        notes.pop("executive_summary", None)
        ctx["notes_by_slot"] = notes


def _is_redundant_note(text: str, finding_tokens: set[str]) -> bool:
    """Heuristic: is this analyst note adding nothing beyond the finding?

    Short notes whose tokens are largely a subset of the deterministic
    finding's tokens are dropped. We keep notes that introduce non-trivial
    new vocabulary (paths, metrics, recommendations).
    """
    note_tokens = _tokens(text)
    if not note_tokens:
        return True
    # Very short notes are most often structural restatements.
    if len(note_tokens) <= 8:
        return True
    if not finding_tokens:
        return False
    # Strip filler tokens — these don't carry meaning either way.
    filler = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "for",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "this",
        "that",
        "it",
        "as",
        "on",
        "by",
        "from",
        "at",
        "score",
        "rule",
        "rules",
        "host",
        "hosts",
    }
    novel = note_tokens - finding_tokens - filler
    return len(novel) < 5


def assemble(artifacts: list[dict]) -> dict:
    """Reshape a wrapper's artifact list into a single-entity bundle.

    Accepts both wrapper inputs (``[scorecard, index]``) and the
    scorecard-brief bundle shape (``{scorecards: [...], index: ...}``)
    produced when render.py promotes a singleton scorecard_brief wrapper.
    """
    # Bundle shape from scorecard_brief.assemble() being routed here.
    if isinstance(artifacts, dict) and "scorecards" in artifacts:
        return _from_brief_bundle(artifacts)

    # Raw wrapper artifact list.
    cards = [
        a for a in artifacts if a.get("schema_version") == "bot_entity_scorecard.v1"
    ]
    index = next(
        (a for a in artifacts if a.get("schema_version") == "bot_scorecard_index.v1"),
        None,
    )
    if not cards:
        raise ValueError(
            "scorecard_entity_review wrapper missing bot_entity_scorecard.v1"
        )
    if len(cards) > 1:
        raise ValueError(
            f"scorecard_entity_review expects exactly one scorecard, got {len(cards)}"
        )
    return {
        "schema_version": SCHEMA,
        "scorecard": cards[0],
        "index": index,
    }


def _from_brief_bundle(bundle: dict) -> dict:
    """Convert the scorecard_brief bundle shape into the entity-review shape."""
    cards = bundle.get("scorecards") or []
    if len(cards) != 1:
        raise ValueError(f"entity_review bundle expects 1 scorecard, got {len(cards)}")
    return {
        "schema_version": SCHEMA,
        "scorecard": cards[0],
        "index": bundle.get("index"),
    }


def prepare(artifact: dict) -> dict:
    """Pure transform from a single-scorecard bundle to template context."""
    sc = artifact.get("scorecard") or artifact
    index = artifact.get("index")

    scope = sc["scope"]
    cluster_label = cluster_display(scope["cluster"])
    entity = sc["entity"]

    score = sc["score"]
    baseline_score = sc.get("baseline_score")
    delta = sc.get("score_delta_points", 0)
    band = sc["band"]
    confidence = sc["confidence"]
    primary_domain = sc.get("primary_domain") or "none"

    rule_results = scorecards_mod.normalize_rule_results(sc)
    triggered = [r for r in rule_results if r.get("status") == "triggered"]
    below_threshold = [r for r in rule_results if r.get("status") == "evaluated_zero"]
    missing = [r for r in rule_results if r.get("status") == "missing_input"]

    triggered_rows = [_triggered_row(r) for r in triggered]
    triggered_by_domain: Counter[str] = Counter(
        (r.get("domain") or "") for r in triggered
    )

    # Fleet context — render only when wrapper actually carries a multi-host
    # index. Lets the reader place this host within a broader review.
    fleet_total = 0
    fleet_rank = None
    if index:
        ranked = index.get("ranked_entities") or []
        fleet_total = len(ranked)
        for r in ranked:
            if r.get("entity") == entity:
                fleet_rank = r.get("rank")
                break

    is_selected_from_fleet = fleet_total > 1

    if is_selected_from_fleet:
        rank_clause = (
            f" (ranked {fleet_rank} of {fleet_total})"
            if fleet_rank
            else f" (1 of {fleet_total})"
        )
    else:
        rank_clause = ""
    headline = f"{cluster_label} — {entity}{rank_clause}"

    rule_counts = scorecards_mod.rule_counts(sc)
    verdict = verdicts_mod.classify(band, rule_counts)
    confidence_chip = verdicts_mod.confidence_chip(rule_counts)
    entity_metrics = sc.get("entity_metrics") or {}
    volume_impact = vi.project_entity(entity_metrics)
    coverage_detail = _coverage_detail(missing)

    dek = _compute_dek(
        verdict,
        score,
        triggered_rows,
        missing,
        entity_metrics,
        is_selected_from_fleet,
        fleet_total,
    )

    deterministic_findings = _build_findings(
        score, delta, triggered_rows, missing, below_threshold, primary_domain
    )

    return {
        "title": "Bot Scorecard Entity Review",
        "kicker": PURPOSE["kicker"],
        "headline": headline,
        "dek": dek,
        # Suppress base.html's purpose strip — orientation moves behind a
        # disclosure inside the content block.
        "purpose": None,
        # Orientation block is rendered behind a disclosure now — daily readers
        # don't see it, first-time readers can expand.
        "orientation": {
            "measures": PURPOSE["measures"],
            "score_legend": PURPOSE["score_legend"],
            "cant_say": PURPOSE["cant_say"],
        },
        "scope": {
            "cluster": scope["cluster"],
            "database": scope["database"],
            "table_used": sc.get("table_used"),
        },
        "windows": _windows(sc, index),
        "entity_summary": {
            "entity": entity,
            "score": score,
            "baseline_score": baseline_score,
            "delta": delta,
            "band": band,
            "confidence": confidence,
            "primary_domain": primary_domain,
            "primary_domain_label": DOMAIN_LABELS.get(primary_domain, primary_domain),
            "evidence_summary": sc.get("evidence_summary") or [],
        },
        "verdict": verdict,
        "confidence_chip": confidence_chip,
        "volume_impact": volume_impact,
        "coverage_detail": coverage_detail,
        "score_summary": _score_summary(sc),
        "rule_counts": rule_counts,
        "deterministic_findings": deterministic_findings,
        "triggered_rules_data": triggered_rows,
        "triggered_by_domain": [
            {
                "domain": d,
                "domain_label": DOMAIN_LABELS.get(d, d),
                "count": c,
            }
            for d, c in triggered_by_domain.most_common()
        ],
        "actions": _actions(sc),
        "fleet_context": {
            "is_selected_from_fleet": is_selected_from_fleet,
            "fleet_total": fleet_total,
            "fleet_rank": fleet_rank,
        },
        "method": {
            "schema_version": artifact.get("schema_version") or SCHEMA,
            "comparison_type": (index or {}).get("comparison_type")
            or sc.get("comparison_type"),
            "producer_limit": (index or {}).get("producer_limit"),
            "result_row_count": 1,
            "result_truncated": False,
            "interpretation_constraints": (
                (index or {}).get("interpretation_constraints")
                or sc.get("interpretation_constraints")
                or []
            ),
        },
        "confidence": {
            "counts": {confidence: 1},
            "reasons": sorted(sc.get("confidence_reasons") or []),
        },
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# ---- helpers ----------------------------------------------------------------


def _build_findings(
    score: int,
    delta: int,
    triggered: list[dict],
    missing: list[dict],
    below_threshold: list[dict],
    primary_domain: str,
) -> list[dict]:
    """Synthesize a Finding-shaped list for the executive summary macro.

    The macro is shared with scorecard_brief and renders ``findings[0]`` as
    the deterministic top-finding paragraph. We surface a one-finding list
    keyed to the host's situation.
    """
    if triggered:
        domain_label = DOMAIN_LABELS.get(primary_domain, primary_domain)
        rule_word = "rule" if len(triggered) == 1 else "rules"
        rule_names = ", ".join(r["name_label"] for r in triggered[:3])
        if len(triggered) > 3:
            rule_names += f", and {len(triggered) - 3} more"
        headline = (
            f"Score {score} — {len(triggered)} {rule_word} triggered in {domain_label}"
        )
        body_parts = [f"Triggered: {rule_names}."]
        if missing:
            body_parts.append(
                f"{len(missing)} additional rule{'s' if len(missing) != 1 else ''} "
                "could not be scored due to missing inputs — treat the score as a "
                "floor on risk, not a complete picture."
            )
        finding = Finding(
            finding_id="entity_review.triggered",
            title=headline,
            headline=headline,
            body=" ".join(body_parts),
            priority=10,
        )
    else:
        headline = f"Score {score} — no rules triggered"
        body = (
            "No mechanical signals crossed threshold for this host in the "
            "current window."
        )
        if missing:
            body += (
                f" {len(missing)} rule{'s' if len(missing) != 1 else ''} "
                "could not be scored due to missing inputs."
            )
        finding = Finding(
            finding_id="entity_review.clean",
            title=headline,
            headline=headline,
            body=body,
            priority=10,
        )
    return [asdict(finding)]


def _triggered_row(rule: dict) -> dict:
    """Project a triggered rule_result into a render-ready dict."""
    domain = rule.get("domain") or ""
    return {
        "name": rule.get("name") or "",
        "name_label": humanize_identifier(rule.get("name") or ""),
        "domain": domain,
        "domain_label": DOMAIN_LABELS.get(domain, domain),
        "threshold": rule.get("threshold"),
        "current": rule.get("current"),
        "baseline": rule.get("baseline"),
        "points": rule.get("points"),
        "evidence": rule.get("evidence") or "",
        "supporting_metrics": rule.get("supporting_metrics") or {},
    }


def _coverage_detail(missing: list[dict]) -> dict | None:
    """Group missing-input rules by domain for the coverage disclosure."""
    if not missing:
        return None
    grouped: dict[str, list[dict]] = {}
    for rule in missing:
        domain = rule.get("domain") or "other"
        grouped.setdefault(domain, []).append(
            {
                "name": rule.get("name") or "",
                "missing_inputs": rule.get("missing_inputs") or [],
            }
        )
    return {
        "total": len(missing),
        "groups": [
            {
                "domain": d,
                "domain_label": DOMAIN_LABELS.get(d, d),
                "rules": sorted(rules, key=lambda r: r["name"]),
            }
            for d, rules in sorted(grouped.items())
        ],
    }


def _compute_dek(
    verdict: dict,
    score: int,
    triggered: list[dict],
    missing: list[dict],
    metrics: dict,
    is_selected_from_fleet: bool,
    fleet_total: int,
) -> str:
    parts = [f"{verdict['label']}."]

    # Lead with the dominant signal where available rather than the score.
    miss_pct = (metrics or {}).get("current_cache_miss_pct")
    base_miss_pct = (metrics or {}).get("baseline_cache_miss_pct")
    if miss_pct is not None and miss_pct >= 50:
        if base_miss_pct is not None and abs(miss_pct - base_miss_pct) < 2:
            parts.append(
                f"Cache miss rate {vi.format_pct(miss_pct)}, persistent vs prior window."
            )
        else:
            parts.append(f"Cache miss rate {vi.format_pct(miss_pct)}.")
    elif triggered:
        n = len(triggered)
        rule_word = "rule" if n == 1 else "rules"
        domains = sorted({r["domain_label"] for r in triggered})
        domain_clause = (
            f" in {domains[0]}"
            if len(domains) == 1
            else f" across {len(domains)} domains"
        )
        parts.append(f"{n} {rule_word} triggered{domain_clause}.")

    if missing:
        if verdict.get("state") == "insufficient_data":
            parts.append(
                f"{len(missing)} rule input{'s' if len(missing) != 1 else ''} "
                "missing — impact cannot be judged from this report alone."
            )
        else:
            parts.append(
                f"{len(missing)} rule{'s' if len(missing) != 1 else ''} "
                "could not be scored."
            )

    if is_selected_from_fleet:
        parts.append(f"Selected from {fleet_total}-host fleet review.")

    parts.append(f"Score {score}.")

    return " ".join(parts)


def _windows(sc: dict, index: dict | None) -> dict | None:
    if index and index.get("current_window") and index.get("baseline_windows"):
        return {
            "current": index["current_window"],
            "baseline": index["baseline_windows"][0],
        }
    if sc.get("current_window") and sc.get("baseline_windows"):
        return {
            "current": sc["current_window"],
            "baseline": sc["baseline_windows"][0],
        }
    return None


def _score_summary(sc: dict) -> dict:
    """Minimal score_summary for the gauge — no histogram, no fleet stats."""
    score = sc["score"]
    baseline = sc.get("baseline_score")
    delta_pct = 0.0
    if baseline:
        delta_pct = (score - baseline) / baseline * 100
    return {
        "lowest": score,
        "median": score,
        "highest": score,
        "distribution": [(score, 1)],
        "bands": {sc["band"]: 1},
        "lowest_delta_pct": delta_pct,
        "scores": [score],
    }


def _actions(sc: dict) -> list[dict]:
    """Project this host's recommended_next_steps into the actions shape.

    Accepts both the structured ``{"summary", "detail"}`` shape that current
    producers emit and the legacy plain-string shape from older artifacts.
    """
    out: list[dict] = []
    for step in sc.get("recommended_next_steps") or []:
        if isinstance(step, dict):
            summary = step.get("summary") or step.get("detail") or ""
            detail = step.get("detail") or step.get("summary") or ""
        else:
            text = str(step)
            summary = text.split(".")[0] + ("." if "." in text else "")
            detail = text
        out.append(
            {
                "summary": summary,
                "detail": detail,
                "step": detail,
                "host_count": 1,
                "preview": sc["entity"],
                "extra": 0,
            }
        )
    return out
