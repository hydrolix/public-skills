"""Context preparer for `bot_posture_movement.v1` — the Bot & Edge
Movement brief.

Mirrors the patterns in ``scorecard_brief.py``: per-item verdict (here
per-metric, not per-host), traffic-weighted lead, italicized clarification
under the bold lead, recommendation/caveat callouts, triage strip with
muted zero-count pills, single source of truth for action selection.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from ..findings import Finding
from ..formatters import format_share_pct
from ..humanize import cluster_display

SCHEMA = "bot_posture_movement.v1"
REPORT_TYPE = "executive_posture"
TEMPLATE = "reports/executive_posture.html"

# Wrapper analyst-note routing. (note_id) -> slot name templates can render.
NOTE_ID_TO_SLOT = {
    "llm-interpretation": "executive_summary",
    "llm-operational": "operational_interpretation",
    "llm-finding-overrides": "finding_overrides",
}

PURPOSE = {
    "report_class_fleet": "Bot Insights — fleet movement brief",
    "report_class_single": "Bot Insights — segment movement brief",
    "measures": (
        "Fleet-wide edge metrics (request volume, bot-like share, cache miss "
        "rate, error rate, 429 rate) compared with the prior equivalent window."
    ),
    "score_legend": (
        "Movement is reported as percent change and percentage-point change. "
        "Confidence is qualitative based on volume and coverage."
    ),
    "cant_say": (
        "Not a root-cause diagnosis. Volume changes do not imply attack or "
        "intent without additional evidence."
    ),
}

# Reader-facing labels for the metric column. Mirrors render_report.METRIC_LABELS
# so the legacy markdown path and the new HTML path read consistently.
METRIC_LABELS = {
    "ai_requests": "AI requests",
    "bot_like_requests": "Bot-like requests",
    "bot_share_pct": "Bot share",
    "cache_misses": "Cache misses",
    "cache_miss_rate_pct": "Cache miss rate",
    "error_5xx_requests": "5xx errors",
    "rate_429_pct": "429 rate",
    "rate_limited_requests": "429 rate-limited requests",
    "requests": "Total requests",
    "avg_bot_score": "Average bot score",
    "siem_auth_fail_requests": "SIEM auth failures",
    "siem_blocked_requests": "SIEM blocked requests",
    "unique_client_ips": "Unique client IPs",
}


# Metric kind (how to express its size, what threshold tier governs it).
# Volume metrics are large-N counts; share metrics are percentages or rates.
_VOLUME_METRICS = frozenset(
    {
        "requests",
        "ai_requests",
        "bot_like_requests",
        "cache_misses",
        "error_5xx_requests",
        "rate_limited_requests",
        "siem_auth_fail_requests",
        "siem_blocked_requests",
        "unique_client_ips",
    }
)

# Verdict thresholds. Investigate is anchored on the operator-action
# thresholds in _metric_recommendation (the plan's recommendation table),
# so the strip and the action selection share one source of truth.
# Watch covers movement above the noise floor but below an action
# threshold; Stable is below the noise floor.
_WATCH_PCT = 10.0
_STABLE_PCT = 5.0


def _metric_label(name: str) -> str:
    return METRIC_LABELS.get(name, name.replace("_", " ").capitalize())


def assemble(artifacts: list[dict]) -> dict:
    """Reassemble a `bot_report_input.v1` wrapper's artifacts into the dict
    shape `prepare()` expects.

    Wrappers carry ``bot_posture_movement.v1`` (required), optionally
    ``bot_mover_attribution.v1`` (top movers for `requests`), and
    optionally ``bot_scorecard_artifacts.v1`` (a packet that nests
    ``index`` + ``scorecards``). Older wrappers may instead emit the
    flat shape (``bot_scorecard_index.v1`` and ``bot_entity_scorecard.v1``
    as separate list entries); handle both.
    """
    posture = next(
        (a for a in artifacts if a.get("schema_version") == "bot_posture_movement.v1"),
        None,
    )
    if posture is None:
        raise ValueError(
            "executive_posture wrapper missing bot_posture_movement.v1 artifact"
        )
    mover = next(
        (a for a in artifacts if a.get("schema_version") == "bot_mover_attribution.v1"),
        None,
    )
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
        scorecards = packet.get("scorecards") or []
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
    return {
        "schema_version": SCHEMA,
        "posture": posture,
        "mover": mover,
        "index": index,
        "scorecards": scorecards,
    }


def prepare(artifact: dict) -> dict:
    posture = artifact["posture"]
    mover = artifact.get("mover")
    scorecards = artifact.get("scorecards") or []

    raw_metrics = posture.get("metrics") or []

    # Compute top mover first so each metric row knows whether a dominant
    # mover concentrates on it. A metric the mover attributes to with ≥ 50%
    # contribution gets an Investigate verdict and a synthesized
    # "investigate the volume mover" recommendation, even when its bare
    # pct_change is below the standard 50% volume threshold — traffic
    # concentration is the operative signal the operator needs to see.
    top_mover = _top_mover(mover)
    metric_rows = [_metric_row(m, top_mover) for m in raw_metrics]

    triage_strip = _triage_strip(metric_rows)
    top_metric = _top_priority_metric(metric_rows, top_mover)
    embedded_scorecards = _embedded_scorecards(scorecards)
    actions = _actions(metric_rows)
    actionable = _actionable_summary(
        metric_rows,
        top_metric,
        top_mover,
        triage_strip,
        actions,
    )

    scope = posture.get("scope") or {}
    cluster_label = _cluster_label(scope, posture)
    current_window = posture.get("current_window") or {}
    baselines = posture.get("baseline_windows") or []
    baseline_window = baselines[0] if baselines else {}

    headline = (
        f"Bot & Edge Movement — {cluster_label}, "
        f"week of {_short_window(current_window)}"
        if cluster_label
        else f"Bot & Edge Movement — week of {_short_window(current_window)}"
    )
    kicker = PURPOSE["report_class_fleet"]
    dek = "How bot traffic and edge health shifted vs the prior week."

    findings = [actionable]

    confidence_reasons = sorted(set(posture.get("confidence_reasons") or []))

    return {
        "title": "Bot & Edge Movement",
        "kicker": kicker,
        "headline": headline,
        "dek": dek,
        "purpose": None,
        "orientation": {
            "measures": PURPOSE["measures"],
            "score_legend": PURPOSE["score_legend"],
            "cant_say": PURPOSE["cant_say"],
        },
        "scope": {
            "cluster": scope.get("cluster") or cluster_label,
            "database": scope.get("database") or "",
            "table_used": posture.get("table_used") or "",
        },
        "windows": {
            "current": current_window,
            "baseline": baseline_window,
        },
        "metrics": metric_rows,
        "top_metric": top_metric,
        "top_mover": top_mover,
        "triage_strip": triage_strip,
        "embedded_scorecards": embedded_scorecards,
        "actions": actions,
        "findings": findings,
        "method": {
            "schema_version": posture.get("schema_version"),
            "comparison_type": posture.get("comparison_type"),
            "producer_limit": None,
            "result_row_count": len(raw_metrics),
            "result_truncated": False,
            "interpretation_constraints": posture.get("interpretation_constraints")
            or [],
        },
        "confidence": {
            "reasons": confidence_reasons,
        },
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# ---- helpers ----------------------------------------------------------------


def _to_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _cluster_label(scope: dict, posture: dict) -> str:
    """Best-effort display label for the H1.

    Falls through: ``scope.cluster`` → ``scope.request_host`` → "" so the
    headline stays sensible whether the producer carries a tenant cluster
    name or only a host scope.
    """
    cluster = scope.get("cluster")
    if cluster:
        return cluster_display(cluster)
    host = scope.get("request_host") or scope.get("entity") or ""
    return host or ""


def _short_window(window: dict) -> str:
    """'2026-04-07 → 2026-04-14' from {start, end} ISO dates.

    Used inside the H1 only; the comparison strip in the header still
    renders the full timestamp via ``window_fmt``.
    """
    if not window:
        return "n/a"
    start = window.get("start") or ""
    end = window.get("end") or ""

    def _date(v: str) -> str:
        if "T" in v:
            return v.split("T", 1)[0]
        return v

    return f"{_date(start)} → {_date(end)}"


def _classify_metric(metric: dict, recommendation: dict | None) -> dict:
    """Per-metric verdict — Investigate / Watch / Stable / Insufficient data.

    Mirrors the fleet brief's 4-state taxonomy so the triage strip / chip
    code paths are shared. The verdict is anchored on the operator-action
    thresholds defined in :func:`_metric_recommendation`: a metric that
    crosses an action threshold with high/medium confidence is
    Investigate. Below the action threshold but with directional movement
    above the noise floor is Watch. |pct_change| < 5% is Stable. Missing
    or unknown direction is Insufficient. Low-confidence movement does
    not demote the row to Insufficient — the coverage gap surfaces via
    the confidence chip, the same axis split the scorecard brief uses.
    """
    direction = (metric.get("direction") or "").lower()
    confidence = (metric.get("confidence") or "").lower()
    pct = _to_float(metric.get("pct_change"))
    abs_pct = abs(pct) if pct is not None else 0.0

    if pct is None or direction in {"", "unknown"}:
        return {
            "state": "insufficient_data",
            "label": "Insufficient data",
            "tone": "neutral",
        }

    if abs_pct < _STABLE_PCT or direction in {"flat", "no_change"}:
        return {
            "state": "stable",
            "label": "Stable",
            "tone": "observe",
        }

    if recommendation is not None and confidence in {"high", "medium"}:
        return {
            "state": "investigate",
            "label": "Investigate",
            "tone": "escalate",
        }

    if abs_pct >= _WATCH_PCT or recommendation is not None:
        return {
            "state": "watch",
            "label": "Watch",
            "tone": "monitor",
        }

    return {
        "state": "watch",
        "label": "Watch",
        "tone": "monitor",
    }


def _metric_recommendation(metric: dict) -> dict | None:
    """Return ``{summary, detail}`` for a metric whose movement crossed an
    operator-action threshold. ``None`` when nothing is actionable.

    Single source of truth for action selection — both the executive
    summary and the actions section consume the same dict, so the short
    form stays consistent with the analyst-grade detail.
    """
    name = metric.get("name") or ""
    direction = (metric.get("direction") or "").lower()
    if direction not in {"increase"}:
        return None
    pct = _to_float(metric.get("pct_change")) or 0.0
    abs_delta = _to_float(metric.get("absolute_delta")) or 0.0

    if name == "requests" and pct >= 50:
        return {
            "summary": "Investigate the volume mover.",
            "detail": (
                "Break down request volume by ASN, host, and bot class for the "
                "affected window."
            ),
            "trigger": "volume mover",
        }
    if name == "bot_share_pct" and abs_delta >= 5:
        return {
            "summary": "Audit the bot-share rise.",
            "detail": (
                "Compare crawler/AI populations vs. prior week; check policy surfaces."
            ),
            "trigger": "crawler shift",
        }
    if name == "rate_429_pct" and abs_delta >= 1:
        return {
            "summary": "Review good-crawler 429s.",
            "detail": (
                "Pull rate-limit policy for known good crawlers and check "
                "policy collateral."
            ),
            "trigger": "rate limiting",
        }
    if name == "error_5xx_requests" and pct >= 25:
        return {
            "summary": "Triage 5xx exposure.",
            "detail": (
                "Group 5xx by origin path and bot class; correlate with deploys."
            ),
            "trigger": "error spike",
        }
    if name == "cache_misses" and pct >= 25:
        return {
            "summary": "Audit cache-key behavior.",
            "detail": (
                "Inspect query-string diversity and cache-key composition for "
                "affected paths."
            ),
            "trigger": "cache movement",
        }
    return None


def _confidence_chip(confidence: str | None) -> dict | None:
    """Surface a chip on metric rows when confidence is below 'high'.

    Mirrors verdicts.confidence_chip — actionability and data-quality are
    different axes; a Watch metric with thin coverage keeps its verdict
    and surfaces the gap as a chip rather than getting demoted to
    Insufficient.
    """
    label = (confidence or "").lower()
    if label == "high":
        return None
    if label == "medium":
        return {"label": "Medium confidence", "tone": "neutral"}
    if label == "low":
        return {"label": "Low confidence", "tone": "neutral"}
    return None


def _metric_row(metric: dict, top_mover: dict | None = None) -> dict:
    """Project a producer metric into the row shape the template renders."""
    name = metric.get("name") or ""
    recommendation = _metric_recommendation(metric)
    verdict = _classify_metric(metric, recommendation)
    # Mover-driven escalation: when a dominant mover (≥ 50% concentration)
    # attributes the move to a single dimension value, treat the metric it
    # explains as Investigate and synthesize the volume-mover action — even
    # if the bare pct_change is below the standard 50% threshold. The
    # operator's job here is to look at the mover, not at the headline %.
    confidence = (metric.get("confidence") or "low").lower()
    direction = (metric.get("direction") or "flat").lower()
    if (
        top_mover
        and top_mover.get("metric_name") == name
        and direction == "increase"
        and confidence in {"high", "medium"}
    ):
        verdict = {
            "state": "investigate",
            "label": "Investigate",
            "tone": "escalate",
        }
        if recommendation is None:
            recommendation = {
                "summary": "Investigate the volume mover.",
                "detail": (
                    "Break down request volume by ASN, host, and bot class for "
                    "the affected window."
                ),
                "trigger": "volume mover",
            }
    return {
        "name": name,
        "label": _metric_label(name),
        "current": _to_float(metric.get("current")),
        "baseline": _to_float(metric.get("baseline")),
        "absolute_delta": _to_float(metric.get("absolute_delta")) or 0.0,
        "pct_change": _to_float(metric.get("pct_change")) or 0.0,
        "direction": (metric.get("direction") or "flat").lower(),
        "confidence": (metric.get("confidence") or "low").lower(),
        "confidence_chip": _confidence_chip(metric.get("confidence")),
        "verdict_state": verdict["state"],
        "verdict_label": verdict["label"],
        "verdict_tone": verdict["tone"],
        "is_volume": name in _VOLUME_METRICS,
        "recommendation_summary": recommendation["summary"] if recommendation else None,
        "recommendation_detail": recommendation["detail"] if recommendation else None,
        "recommendation_trigger": (
            recommendation["trigger"] if recommendation else None
        ),
        # Magnitude key used to rank "largest move" — abs_delta for volume
        # metrics, abs(pct_change) for share-style metrics.
        "magnitude": (
            abs(_to_float(metric.get("absolute_delta")) or 0.0)
            if name in _VOLUME_METRICS
            else abs(_to_float(metric.get("pct_change")) or 0.0)
        ),
    }


# Verdict ordering for the strip and the table (Investigate first).
_STATE_ORDER = ("investigate", "watch", "stable", "insufficient_data")
_STATE_LABELS = {
    "investigate": "Investigate",
    "watch": "Watch",
    "stable": "Stable",
    "insufficient_data": "Insufficient data",
}
_STATE_TONE = {
    "investigate": "escalate",
    "watch": "monitor",
    "stable": "observe",
    "insufficient_data": "neutral",
}


def _triage_strip(metric_rows: list[dict]) -> dict:
    """Per-metric verdict counts in the same shape the scorecard brief's
    triage strip expects (``pills`` + ``rationale`` + ``counts``).
    """
    counts: Counter = Counter(r["verdict_state"] for r in metric_rows)
    for state in _STATE_ORDER:
        counts.setdefault(state, 0)

    pills = [
        {
            "state": state,
            "label": _STATE_LABELS[state],
            "tone": _STATE_TONE[state],
            "count": counts.get(state, 0),
        }
        for state in _STATE_ORDER
    ]

    n_total = len(metric_rows)
    n_inv = counts.get("investigate", 0)
    n_watch = counts.get("watch", 0)
    n_stable = counts.get("stable", 0)
    n_insufficient = counts.get("insufficient_data", 0)
    parts: list[str] = []
    if n_inv:
        parts.append(
            f"{n_inv} metric needs attention"
            if n_inv == 1
            else f"{n_inv} metrics need attention"
        )
    if n_watch:
        parts.append(f"{n_watch} to watch")
    if n_insufficient:
        parts.append(f"{n_insufficient} cannot be judged from this report alone")
    if not parts and n_stable:
        parts.append(
            "the metric is stable"
            if n_stable == 1
            else f"all {n_stable} metrics stable"
        )
    rationale = (
        "; ".join(parts) + (f" (out of {n_total})." if n_total else ".")
        if parts
        else ""
    )

    return {
        "pills": pills,
        "rationale": rationale,
        "counts": dict(counts),
    }


def _top_priority_metric(
    metric_rows: list[dict], top_mover: dict | None = None
) -> dict | None:
    """Pick the metric that should anchor the executive summary lead.

    When a dominant mover (≥ 50% concentration) attributes the move to a
    single metric, that metric anchors the lead — traffic concentration
    is the most readable signal the operator needs first. Otherwise fall
    back to the largest-magnitude Investigate (then Watch) row with
    confidence ≥ medium.
    """
    if top_mover:
        for r in metric_rows:
            if r["name"] == top_mover.get("metric_name") and r["verdict_state"] in {
                "investigate",
                "watch",
            }:
                return r
    candidates = [
        r
        for r in metric_rows
        if r["verdict_state"] == "investigate" and r["confidence"] in {"high", "medium"}
    ]
    if not candidates:
        candidates = [
            r
            for r in metric_rows
            if r["verdict_state"] in {"investigate", "watch"}
            and r["confidence"] in {"high", "medium"}
        ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r["magnitude"])


def _top_mover(mover: dict | None) -> dict | None:
    """Surface the top mover when its `contribution_pct` ≥ 50%.

    Analog of the scorecard brief's ``shared_signal``: when one entity
    (ASN, host, bot class) carries most of a metric's movement, lead with
    that — investigate as one cause, not N independent ones.
    """
    if not mover:
        return None
    movers = mover.get("movers") or []
    if not movers:
        return None
    top = movers[0]
    contribution = _to_float(top.get("contribution_pct"))
    if contribution is None or contribution < 50:
        return None
    metric_name = mover.get("metric") or top.get("metric") or "requests"
    metric_label = _metric_label(metric_name)
    dimension_label = (mover.get("dimension") or "").replace("_", " ")
    value = top.get("value") or ""
    pretty_dim = (dimension_label or "Dimension").upper().replace("CLIENT ASN", "ASN")
    headline = (
        f"{pretty_dim} {value} covers "
        f"{format_share_pct(contribution)} of the {metric_label.lower()} move"
    )
    return {
        "metric_name": metric_name,
        "metric_label": metric_label,
        "dimension": mover.get("dimension"),
        "dimension_label": pretty_dim,
        "value": value,
        "contribution_pct": contribution,
        "absolute_delta": _to_float(top.get("absolute_delta")),
        "pct_change": _to_float(top.get("pct_change")),
        "headline": headline,
        "movers": movers,
    }


def _embedded_scorecards(scorecards: list[dict]) -> list[dict]:
    """Compact rollup rows when the wrapper bundles
    ``bot_scorecard_artifacts.v1``. Per-host scoring detail lives in the
    scorecard brief; this section only cross-references which hosts the
    movement applies to.
    """
    rows: list[dict] = []
    for sc in scorecards:
        rule_results = sc.get("rule_results") or []
        triggered = sum(1 for r in rule_results if r.get("status") == "triggered")
        rows.append(
            {
                "entity": sc.get("entity") or "",
                "score": sc.get("score"),
                "band": sc.get("band") or "",
                "primary_domain": sc.get("primary_domain") or "",
                "triggered_count": triggered,
                "verdict_label": _band_verdict_label(sc.get("band") or "", triggered),
                "verdict_tone": _band_verdict_tone(sc.get("band") or ""),
            }
        )
    rows.sort(key=lambda r: (r.get("score") or 100,))
    return rows


def _band_verdict_label(band: str, triggered: int) -> str:
    if band in {"urgent_review", "high_review"}:
        return "Assign"
    if band in {"medium_review", "low_review"} and triggered:
        return "Assign"
    if triggered:
        return "Watch"
    return "Close — expected"


def _band_verdict_tone(band: str) -> str:
    if band in {"urgent_review", "high_review"}:
        return "escalate"
    if band in {"medium_review", "low_review"}:
        return "monitor"
    return "observe"


def _actions(metric_rows: list[dict]) -> list[dict]:
    """Flatten per-metric recommendations into the same action shape the
    scorecard brief produces. Investigate metrics rank ahead of Watch.

    Each action entry carries both ``summary`` (executive-grade short
    form) and ``detail`` (analyst-grade) so downstream consumers don't
    re-derive copy. ``host_count`` reads as "metrics affected" in this
    report — the actions macro renders it as "N · preview".
    """
    state_rank = {"investigate": 0, "watch": 1, "stable": 2, "insufficient_data": 3}
    actionable = [
        r
        for r in metric_rows
        if r["recommendation_summary"]
        and r["verdict_state"] in {"investigate", "watch"}
    ]
    actionable.sort(
        key=lambda r: (state_rank.get(r["verdict_state"], 9), -r["magnitude"])
    )
    out: list[dict] = []
    for r in actionable:
        out.append(
            {
                "summary": r["recommendation_summary"],
                "detail": r["recommendation_detail"],
                "step": r["recommendation_detail"],
                "host_count": 1,
                "preview": r["label"],
                "extra": 0,
                "trigger": r["recommendation_trigger"],
                "metric_name": r["name"],
            }
        )
    return out


def _actionable_summary(
    metric_rows: list[dict],
    top_metric: dict | None,
    top_mover: dict | None,
    triage_strip: dict,
    actions: list[dict],
) -> Finding:
    """Synthesize the executive-summary lead Finding.

    The headline names the dominant move (with traffic-weighted framing
    when a top mover is present); the body italicizes the queue-state
    clarification on its own line; ``recommendation`` carries the short
    form of the top metric's action; ``caveat`` fires on coverage gaps.
    """
    counts = triage_strip.get("counts", {})
    n_total = len(metric_rows)
    n_inv = counts.get("investigate", 0)
    n_watch = counts.get("watch", 0)
    n_insufficient = counts.get("insufficient_data", 0)

    headline = _headline_for(top_metric, top_mover, n_inv, n_watch, n_total)

    body_parts: list[str] = []
    inv_clause = (
        f"{n_inv} metric needs attention"
        if n_inv == 1
        else f"{n_inv} metrics need attention"
    )
    watch_clause = f"{n_watch} to watch" if n_watch else ""
    if n_inv and n_watch:
        body_parts.append(f"{inv_clause}; {watch_clause}")
    elif n_inv:
        body_parts.append(inv_clause)
    elif n_watch:
        body_parts.append(
            f"{n_watch} metric needs watching"
            if n_watch == 1
            else f"{n_watch} metrics to watch"
        )
    if n_insufficient:
        body_parts.append(f"{n_insufficient} cannot be judged from this report alone")
    body = "; ".join(body_parts) + "." if body_parts else ""

    recommendation: str | None = None
    if actions:
        top_action = actions[0]
        short = (top_action.get("summary") or top_action.get("detail") or "").rstrip(
            "."
        )
        recommendation = f"{short}." if short else None

    caveat = _coverage_caveat(metric_rows)

    return Finding(
        finding_id="actionable_summary",
        title=headline,
        headline=headline,
        body=body,
        recommendation=recommendation,
        caveat=caveat,
        priority=100,
    )


def _headline_for(
    top_metric: dict | None,
    top_mover: dict | None,
    n_inv: int,
    n_watch: int,
    n_total: int,
) -> str:
    """The single bold line that opens the executive summary."""
    if top_metric is None:
        if n_total:
            return f"{n_total} metric{'s' if n_total != 1 else ''} reviewed — no material movement"
        return "No metrics in this artifact"
    label = top_metric["label"]
    direction_word = "up" if top_metric["direction"] == "increase" else "down"
    if top_metric["is_volume"]:
        magnitude = (
            f"{top_metric['pct_change']:+.0f}%".replace("+-", "-")
            if top_metric["pct_change"] is not None
            else ""
        )
        lead = f"{label} {direction_word} {magnitude} week-over-week"
    else:
        # Share-style — prefer pp framing when both are available.
        if top_metric.get("absolute_delta") is not None:
            pp = abs(top_metric["absolute_delta"])
            lead = (
                f"{label} {direction_word} {pp:.1f}pp week-over-week"
                if pp >= 1
                else f"{label} {direction_word} {pp:.2f}pp week-over-week"
            )
        else:
            magnitude = f"{top_metric['pct_change']:+.0f}%".replace("+-", "-")
            lead = f"{label} {direction_word} {magnitude} week-over-week"
    # Append traffic-weighted framing when a dominant mover is present
    # AND it's about the metric we're leading with.
    if top_mover and top_mover.get("metric_name") == top_metric["name"]:
        return (
            f"{lead} — {top_mover['dimension_label']} {top_mover['value']} covers "
            f"{format_share_pct(top_mover['contribution_pct'])} of the increase"
        )
    return lead


def _coverage_caveat(metric_rows: list[dict]) -> str | None:
    """Coverage-thin caveat when ≥ 50% of metrics carry low confidence
    or the artifact includes no comparable baseline.

    Phrasing matches the scorecard brief's caveat copy ("Real movement
    may be larger than the visible delta.") so the two reports read
    consistently.
    """
    if not metric_rows:
        return None
    low_conf = sum(1 for r in metric_rows if r["confidence"] == "low")
    insufficient = sum(
        1 for r in metric_rows if r["verdict_state"] == "insufficient_data"
    )
    suspect = low_conf + insufficient
    if suspect == 0:
        return None
    pct = 100 * suspect / len(metric_rows)
    if pct >= 50:
        return (
            f"Coverage is thin — {pct:.0f}% of metrics had low or insufficient "
            "confidence. Real movement may be larger than the visible delta."
        )
    return None
