#!/usr/bin/env python3
"""Emit deterministic Bot Insights entity scorecards from aggregate JSON.

This script does not query Hydrolix. Feed it Hydrolix MCP query results, saved
JSON, or pasted aggregate JSON that already contains entity-level aggregate
rows. Hydrolix should do filtering, grouping, and aggregation; this script
standardizes rule-based scorecard shape, feature evidence, confidence reasons,
and ranked index output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable


SCORECARD_SCHEMA = "bot_entity_scorecard.v1"
INDEX_SCHEMA = "bot_scorecard_index.v1"
ARTIFACT_SCHEMA = "bot_scorecard_artifacts.v1"
SCORECARD_ERROR_SCHEMA = "bot_scorecard_error.v1"
ADVANCED_ATTRIBUTION_SCHEMA = "bot_attribution_report.v1"
ADVANCED_SCORECARD_INPUT_SCHEMA = "bot_scorecard_input.v1"

SUPPORTED_ENTITY_TYPES = (
    "client_asn",
    "request_path_norm",
    "request_host",
    "bot_class",
    "ai_category",
)

DOMAINS = (
    "movement",
    "origin_impact",
    "cache_busting",
    "crawler_governance",
    "security_evidence",
    "signal_alignment",
    "policy_collateral",
)

INTERPRETATION_CONSTRAINTS = [
    "rule_based_scorecard",
    "mechanical_features_only",
    "no_causal_claim",
    "llm_may_summarize_structured_evidence_only",
]

METADATA_KEYS = {
    "period",
    "timestamp",
    "time",
    "bucket",
    "window",
    "label",
    "dimension",
    "value",
}

SIEM_INPUTS = {
    "siem_blocked_requests",
    "cnt_blocked",
    "blocked_requests",
    "siem_auth_fail_requests",
    "cnt_auth_fail",
    "auth_fail_requests",
}


class InvalidScorecardInputError(ValueError):
    """Typed invalid-input error for scorecard library callers."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str = "$",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        error = {
            "code": code,
            "message": message,
            "path": path,
        }
        if details:
            error["details"] = details
        self.document = {
            "schema_version": SCORECARD_ERROR_SCHEMA,
            "error_type": "invalid_input",
            "fatal": True,
            "errors": [error],
            "limitations": [],
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute Bot Insights scorecard artifacts from aggregate JSON."
    )
    parser.add_argument(
        "text",
        nargs="*",
        help="Aggregate JSON. If omitted, stdin is read.",
    )
    parser.add_argument(
        "-f",
        "--file",
        type=Path,
        help="Read aggregate JSON from a file instead of positional arguments/stdin.",
    )
    parser.add_argument(
        "--entity-type",
        choices=SUPPORTED_ENTITY_TYPES,
        help="Entity type to score. Defaults to metadata or inferred row columns.",
    )
    parser.add_argument(
        "--min-count",
        type=float,
        default=100.0,
        help="Minimum current and baseline support count for high confidence.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional maximum number of scorecards and ranked index entries.",
    )
    parser.add_argument(
        "--output",
        choices=("all", "scorecards", "index"),
        default="all",
        help="Artifact type to emit.",
    )
    return parser.parse_args()


def read_input(args: argparse.Namespace) -> str:
    if args.file:
        return args.file.read_text(encoding="utf-8")
    if args.text:
        return " ".join(args.text)
    return sys.stdin.read()


def to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def clean_number(value: float | int | None) -> float | int | None:
    if value is None:
        return None
    rounded = round(float(value), 6)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def pct_delta(current: float, baseline: float) -> float:
    return (current - baseline) / max(baseline, 1.0) * 100.0


def column_names(columns: list[Any]) -> list[str]:
    names: list[str] = []
    for column in columns:
        if isinstance(column, str):
            names.append(column)
        elif isinstance(column, dict):
            names.append(str(column.get("name") or column.get("column") or ""))
        else:
            names.append(str(column))
    return names


def result_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]

    if not isinstance(value, dict):
        return []

    rows = value.get("rows")
    if not isinstance(rows, list):
        rows = value.get("data")
    if not isinstance(rows, list):
        return []

    if not rows:
        return []
    if all(isinstance(row, dict) for row in rows):
        return rows

    columns = value.get("columns", [])
    if not isinstance(columns, list):
        return []
    names = column_names(columns)
    converted: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, list):
            converted.append({name: row[index] for index, name in enumerate(names) if index < len(row)})
    return converted


def infer_entity_type(row: dict[str, Any], requested: str | None = None) -> str:
    if requested:
        return requested
    for entity_type in SUPPORTED_ENTITY_TYPES:
        if entity_type in row:
            return entity_type
    if "entity_type" in row and str(row["entity_type"]) in SUPPORTED_ENTITY_TYPES:
        return str(row["entity_type"])
    if "dimension" in row and str(row["dimension"]) in SUPPORTED_ENTITY_TYPES:
        return str(row["dimension"])
    return "value"


def entity_value(row: dict[str, Any], entity_type: str) -> str:
    if entity_type in row:
        return str(row[entity_type])
    if "entity" in row:
        return str(row["entity"])
    if "value" in row:
        return str(row["value"])
    return ""


def prefixed_keys(prefix: str, names: tuple[str, ...]) -> tuple[str, ...]:
    keys: list[str] = []
    for name in names:
        keys.extend(
            [
                f"{prefix}_{name}",
                f"{name}_{prefix}",
                f"{prefix}.{name}",
            ]
        )
    return tuple(keys)


def first_number(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in row:
            value = to_number(row[key])
            if value is not None:
                return value
    return None


def current_number(row: dict[str, Any], *names: str) -> float | None:
    return first_number(row, prefixed_keys("current", names) + names)


def baseline_number(row: dict[str, Any], *names: str) -> float | None:
    return first_number(row, prefixed_keys("baseline", names))


def count_values(row: dict[str, Any]) -> tuple[float | None, float | None]:
    current = first_number(
        row,
        (
            "current_count",
            "current_requests",
            "requests_current",
            "current.cnt_all",
            "current_cnt_all",
            "cnt_all_current",
            "requests",
            "cnt_all",
            "current",
        ),
    )
    baseline = first_number(
        row,
        (
            "baseline_count",
            "baseline_requests",
            "requests_baseline",
            "baseline.cnt_all",
            "baseline_cnt_all",
            "cnt_all_baseline",
            "baseline",
        ),
    )
    return current, baseline


def combine_period_rows(rows: list[dict[str, Any]], entity_type: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    saw_period = False
    saw_non_period = False

    for row in rows:
        period = str(row.get("period", "")).lower()
        if period not in {"current", "baseline", "after", "before"}:
            saw_non_period = True
            continue
        saw_period = True
        normalized_period = "current" if period == "after" else "baseline" if period == "before" else period
        row_entity_type = infer_entity_type(row, entity_type if entity_type != "value" else None)
        entity = entity_value(row, row_entity_type)
        key = (row_entity_type, entity)
        combined = grouped.setdefault(key, {row_entity_type: entity})
        for field, value in row.items():
            if field in METADATA_KEYS or field in SUPPORTED_ENTITY_TYPES:
                continue
            combined[f"{normalized_period}_{field}"] = value

    if not saw_period:
        return rows
    if saw_non_period:
        raise ValueError(
            "Input rows must not mix period-split rows with already-combined "
            "entity rows. Normalize or join rows before running scorecard.py."
        )
    return list(grouped.values())


def metadata_from(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    metadata = dict(value.get("confidence_context", {}) if isinstance(value.get("confidence_context"), dict) else {})
    for key in (
        "scope",
        "comparison_type",
        "granularity",
        "table_used",
        "current_window",
        "baseline_windows",
        "summary_table_used",
        "source_coverage_caveat",
        "source_caveats",
        "rowset_complete",
        "contribution_basis",
    ):
        if key in value:
            metadata[key] = value[key]
    return metadata


def prepared_rows(value: Any, entity_type: str | None = None) -> tuple[list[dict[str, Any]], str]:
    rows = result_rows(value)
    if not rows and isinstance(value, dict):
        rows = [value]

    requested = entity_type
    if requested is None and isinstance(value, dict):
        candidate = value.get("entity_type") or value.get("dimension")
        if str(candidate) in SUPPORTED_ENTITY_TYPES:
            requested = str(candidate)

    inferred = requested or (infer_entity_type(rows[0]) if rows else "value")
    rows = combine_period_rows(rows, inferred)
    if rows and inferred == "value":
        inferred = infer_entity_type(rows[0])
    return rows, inferred


def metric_values(row: dict[str, Any], metric: tuple[str, ...]) -> tuple[float | None, float | None]:
    return current_number(row, *metric), baseline_number(row, *metric)


def make_feature(
    name: str,
    domain: str,
    points: int,
    evidence: str,
    *,
    current: float | None = None,
    baseline: float | None = None,
    threshold: float | None = None,
    supporting_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    feature: dict[str, Any] = {
        "name": name,
        "domain": domain,
        "points": points,
        "evidence": evidence,
    }
    if current is not None:
        feature["current"] = clean_number(current)
    if baseline is not None:
        feature["baseline"] = clean_number(baseline)
    if threshold is not None:
        feature["threshold"] = clean_number(threshold)
    if supporting_metrics:
        feature["supporting_metrics"] = supporting_metrics
    return feature


def missing_feature(name: str, domain: str, missing_inputs: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "domain": domain,
        "missing_inputs": sorted(set(missing_inputs)),
        "reason": "feature_input_missing",
    }


def eval_new_entity(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    current, baseline = count_values(row)
    if current is None or baseline is None:
        return None, missing_feature("new_entity", "movement", ["current_requests", "baseline_requests"])
    if baseline < 1 and current > 0:
        return make_feature(
            "new_entity",
            "movement",
            12,
            f"Entity has {clean_number(current)} current requests and no baseline support.",
            current=current,
            baseline=baseline,
            threshold=1,
        ), None
    return None, None


def eval_volume_delta_high(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    current, baseline = count_values(row)
    if current is None or baseline is None:
        return None, missing_feature("volume_delta_high", "movement", ["current_requests", "baseline_requests"])
    delta = current - baseline
    change = pct_delta(current, baseline)
    if delta >= 100 and change >= 100:
        return make_feature(
            "volume_delta_high",
            "movement",
            12,
            f"Request volume increased by {clean_number(delta)} ({clean_number(change)}%).",
            current=current,
            baseline=baseline,
            threshold=100,
            supporting_metrics={"absolute_delta": clean_number(delta), "pct_change": clean_number(change)},
        ), None
    return None, None


def eval_contribution_to_total_delta_high(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    contribution = first_number(row, ("contribution_pct", "contribution_to_total_delta_pct"))
    if contribution is None:
        return None, missing_feature(
            "contribution_to_total_delta_high",
            "movement",
            ["contribution_pct"],
        )
    if contribution >= 20:
        return make_feature(
            "contribution_to_total_delta_high",
            "movement",
            10,
            f"Entity contributes {clean_number(contribution)}% of the total absolute delta.",
            current=contribution,
            threshold=20,
        ), None
    return None, None


def eval_bot_share_delta_high(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    current, baseline = metric_values(row, ("bot_share_pct", "bot_pct"))
    if current is None or baseline is None:
        return None, missing_feature("bot_share_delta_high", "movement", ["current_bot_share_pct", "baseline_bot_share_pct"])
    delta = current - baseline
    if delta >= 10:
        return make_feature(
            "bot_share_delta_high",
            "movement",
            8,
            f"Bot share increased by {clean_number(delta)} percentage points.",
            current=current,
            baseline=baseline,
            threshold=10,
            supporting_metrics={"absolute_delta_points": clean_number(delta)},
        ), None
    return None, None


def eval_cache_miss_rate_high(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    current = current_number(row, "cache_miss_pct", "miss_rate_pct")
    if current is None:
        misses = current_number(row, "cache_misses", "cnt_cache_miss")
        requests, _ = count_values(row)
        if misses is not None and requests and requests > 0:
            current = misses / requests * 100.0
    if current is None:
        return None, missing_feature("cache_miss_rate_high", "cache_busting", ["cache_miss_pct"])
    if current >= 50:
        return make_feature(
            "cache_miss_rate_high",
            "cache_busting",
            10,
            f"Cache miss rate is {clean_number(current)}%.",
            current=current,
            threshold=50,
        ), None
    return None, None


def eval_cache_miss_delta_high(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    current, baseline = metric_values(row, ("cache_miss_pct", "miss_rate_pct"))
    if current is None or baseline is None:
        return None, missing_feature("cache_miss_delta_high", "cache_busting", ["current_cache_miss_pct", "baseline_cache_miss_pct"])
    delta = current - baseline
    if delta >= 15:
        return make_feature(
            "cache_miss_delta_high",
            "cache_busting",
            8,
            f"Cache miss rate increased by {clean_number(delta)} percentage points.",
            current=current,
            baseline=baseline,
            threshold=15,
            supporting_metrics={"absolute_delta_points": clean_number(delta)},
        ), None
    return None, None


def eval_origin_p95_delta_high(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    current, baseline = metric_values(row, ("origin_p95_ms", "p95_origin_ttfb", "origin_p95_ttfb_ms"))
    if current is None or baseline is None:
        return None, missing_feature("origin_p95_delta_high", "origin_impact", ["current_origin_p95_ms", "baseline_origin_p95_ms"])
    delta = current - baseline
    change = pct_delta(current, baseline)
    if delta >= 100 and change >= 25:
        return make_feature(
            "origin_p95_delta_high",
            "origin_impact",
            10,
            f"Origin p95 increased by {clean_number(delta)} ms ({clean_number(change)}%).",
            current=current,
            baseline=baseline,
            threshold=100,
            supporting_metrics={"absolute_delta_ms": clean_number(delta), "pct_change": clean_number(change)},
        ), None
    return None, None


def eval_origin_cost_contribution_high(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    contribution = current_number(row, "origin_cost_contribution_pct", "origin_cost_pct")
    if contribution is None:
        return None, missing_feature("origin_cost_contribution_high", "origin_impact", ["origin_cost_contribution_pct"])
    if contribution >= 20:
        return make_feature(
            "origin_cost_contribution_high",
            "origin_impact",
            18,
            f"Entity contributes {clean_number(contribution)}% of origin cost proxy.",
            current=contribution,
            threshold=20,
        ), None
    return None, None


def eval_querystring_diversity_high(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    ratio = current_number(row, "qs_diversity_ratio", "querystring_diversity_ratio")
    if ratio is None:
        return None, missing_feature("querystring_diversity_high", "cache_busting", ["qs_diversity_ratio"])
    if ratio >= 0.5:
        return make_feature(
            "querystring_diversity_high",
            "cache_busting",
            16,
            f"Query-string diversity ratio is {clean_number(ratio)}.",
            current=ratio,
            threshold=0.5,
        ), None
    return None, None


def eval_querystring_diversity_with_high_miss_rate(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    ratio = current_number(row, "qs_diversity_ratio", "querystring_diversity_ratio")
    miss_rate = current_number(row, "cache_miss_pct", "miss_rate_pct")
    if ratio is None or miss_rate is None:
        missing = []
        if ratio is None:
            missing.append("qs_diversity_ratio")
        if miss_rate is None:
            missing.append("cache_miss_pct")
        return None, missing_feature("querystring_diversity_with_high_miss_rate", "cache_busting", missing)
    if ratio >= 0.5 and miss_rate >= 50:
        return make_feature(
            "querystring_diversity_with_high_miss_rate",
            "cache_busting",
            18,
            f"High query-string diversity coincides with {clean_number(miss_rate)}% cache misses.",
            current=ratio,
            threshold=0.5,
            supporting_metrics={"cache_miss_pct": clean_number(miss_rate), "cache_miss_threshold": 50},
        ), None
    return None, None


def eval_rate_429_delta_high(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    current, baseline = metric_values(row, ("rate_429_pct", "rate_limited_pct"))
    if current is None or baseline is None:
        return None, missing_feature("rate_429_delta_high", "crawler_governance", ["current_rate_429_pct", "baseline_rate_429_pct"])
    delta = current - baseline
    if delta >= 5:
        return make_feature(
            "rate_429_delta_high",
            "crawler_governance",
            8,
            f"429 rate increased by {clean_number(delta)} percentage points.",
            current=current,
            baseline=baseline,
            threshold=5,
            supporting_metrics={"absolute_delta_points": clean_number(delta)},
        ), None
    return None, None


def eval_rate_5xx_delta_high(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    current, baseline = metric_values(row, ("rate_5xx_pct", "error_5xx_pct"))
    if current is None or baseline is None:
        return None, missing_feature("rate_5xx_delta_high", "crawler_governance", ["current_rate_5xx_pct", "baseline_rate_5xx_pct"])
    delta = current - baseline
    if delta >= 5:
        return make_feature(
            "rate_5xx_delta_high",
            "crawler_governance",
            8,
            f"5xx rate increased by {clean_number(delta)} percentage points.",
            current=current,
            baseline=baseline,
            threshold=5,
            supporting_metrics={"absolute_delta_points": clean_number(delta)},
        ), None
    return None, None


def eval_good_bot_429_present(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    good_429 = current_number(row, "good_bot_429_requests", "good_bot_rate_limited_429", "good_bot_429")
    if good_429 is None:
        return None, missing_feature("good_bot_429_present", "crawler_governance", ["good_bot_429_requests"])
    if good_429 > 0:
        return make_feature(
            "good_bot_429_present",
            "crawler_governance",
            14,
            f"Good bot traffic has {clean_number(good_429)} 429 responses.",
            current=good_429,
            threshold=0,
        ), None
    return None, None


def eval_good_bot_error_rate_high(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    rate = current_number(row, "good_bot_error_rate_pct", "good_bot_errors_pct")
    if rate is None:
        return None, missing_feature("good_bot_error_rate_high", "crawler_governance", ["good_bot_error_rate_pct"])
    if rate >= 5:
        return make_feature(
            "good_bot_error_rate_high",
            "crawler_governance",
            12,
            f"Good bot error rate is {clean_number(rate)}%.",
            current=rate,
            threshold=5,
        ), None
    return None, None


def eval_policy_surface_failure_present(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    failures = current_number(
        row,
        "policy_surface_failures",
        "governance_surface_failures",
        "robots_llms_failures",
    )
    if failures is None:
        return None, missing_feature("policy_surface_failure_present", "crawler_governance", ["policy_surface_failures"])
    if failures > 0:
        return make_feature(
            "policy_surface_failure_present",
            "crawler_governance",
            16,
            f"Governance surfaces have {clean_number(failures)} failed requests.",
            current=failures,
            threshold=0,
        ), None
    return None, None


def eval_ai_crawler_growth_high(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    current, baseline = metric_values(row, ("ai_crawler_requests", "ai_requests", "ai_crawler_share_pct"))
    if current is None or baseline is None:
        return None, missing_feature("ai_crawler_growth_high", "crawler_governance", ["current_ai_crawler_requests", "baseline_ai_crawler_requests"])
    delta = current - baseline
    change = pct_delta(current, baseline)
    if delta > 0 and change >= 100:
        return make_feature(
            "ai_crawler_growth_high",
            "crawler_governance",
            10,
            f"AI crawler metric increased by {clean_number(change)}%.",
            current=current,
            baseline=baseline,
            threshold=100,
            supporting_metrics={"absolute_delta": clean_number(delta), "pct_change": clean_number(change)},
        ), None
    return None, None


def eval_siem_blocked_present(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    blocked = current_number(row, "siem_blocked_requests", "cnt_blocked", "blocked_requests")
    if blocked is None:
        return None, missing_feature("siem_blocked_present", "security_evidence", ["siem_blocked_requests"])
    if blocked > 0:
        return make_feature(
            "siem_blocked_present",
            "security_evidence",
            12,
            f"SIEM summary reports {clean_number(blocked)} blocked requests.",
            current=blocked,
            threshold=0,
        ), None
    return None, None


def eval_siem_auth_fail_present(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    failures = current_number(row, "siem_auth_fail_requests", "cnt_auth_fail", "auth_fail_requests")
    if failures is None:
        return None, missing_feature("siem_auth_fail_present", "security_evidence", ["siem_auth_fail_requests"])
    if failures > 0:
        return make_feature(
            "siem_auth_fail_present",
            "security_evidence",
            12,
            f"SIEM summary reports {clean_number(failures)} auth failures.",
            current=failures,
            threshold=0,
        ), None
    return None, None


def eval_bad_bot_share_high(row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    share = current_number(row, "bad_bot_share_pct", "bad_bot_pct")
    if share is None:
        return None, missing_feature("bad_bot_share_high", "security_evidence", ["bad_bot_share_pct"])
    if share >= 50:
        return make_feature(
            "bad_bot_share_high",
            "security_evidence",
            14,
            f"Bad bot share is {clean_number(share)}%.",
            current=share,
            threshold=50,
        ), None
    return None, None


FeatureEvaluator = Callable[[dict[str, Any]], tuple[dict[str, Any] | None, dict[str, Any] | None]]

FEATURE_EVALUATORS: tuple[FeatureEvaluator, ...] = (
    eval_new_entity,
    eval_volume_delta_high,
    eval_contribution_to_total_delta_high,
    eval_bot_share_delta_high,
    eval_cache_miss_rate_high,
    eval_cache_miss_delta_high,
    eval_origin_p95_delta_high,
    eval_origin_cost_contribution_high,
    eval_querystring_diversity_high,
    eval_querystring_diversity_with_high_miss_rate,
    eval_rate_429_delta_high,
    eval_rate_5xx_delta_high,
    eval_good_bot_429_present,
    eval_good_bot_error_rate_high,
    eval_policy_surface_failure_present,
    eval_ai_crawler_growth_high,
    eval_siem_blocked_present,
    eval_siem_auth_fail_present,
    eval_bad_bot_share_high,
)


def score_band(score: int) -> str:
    if score >= 80:
        return "urgent_review"
    if score >= 60:
        return "high_review"
    if score >= 40:
        return "medium_review"
    if score >= 20:
        return "low_review"
    return "observe"


def siem_inputs_available(row: dict[str, Any]) -> bool:
    return current_number(row, *tuple(SIEM_INPUTS)) is not None


def confidence(
    row: dict[str, Any],
    metadata: dict[str, Any],
    not_evaluated: list[dict[str, Any]],
    min_count: float,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    table_used = str(metadata.get("table_used", ""))
    summary_table_used = metadata.get("summary_table_used")
    if summary_table_used is None:
        summary_table_used = bool(table_used and table_used not in {"bot_detection", "bot_detection_siem"})

    if summary_table_used:
        reasons.append("summary_table_used")
        reasons.append("retained_dimensions_fit")
    else:
        reasons.append("raw_table_fallback")

    current_count, baseline_count = count_values(row)
    sparse = False
    if current_count is not None:
        if current_count >= min_count:
            reasons.append("current_count_sufficient")
        else:
            sparse = True
    if baseline_count is not None:
        if baseline_count >= min_count:
            reasons.append("baseline_count_sufficient")
        else:
            sparse = True
    if sparse:
        reasons.append("sparse_counts")
    if baseline_count is not None and baseline_count < 1:
        reasons.append("zero_baseline_guard")
    if metadata.get("source_coverage_caveat") or metadata.get("source_caveats"):
        reasons.append("source_coverage_caveat")
    if not siem_inputs_available(row):
        reasons.append("siem_unavailable")
    if not_evaluated:
        reasons.append("feature_input_missing")

    low_reasons = {"raw_table_fallback", "sparse_counts"}
    if any(reason in reasons for reason in low_reasons):
        label = "low"
    elif "source_coverage_caveat" in reasons or "siem_unavailable" in reasons or "feature_input_missing" in reasons:
        label = "medium"
    else:
        label = "high"

    return label, reasons


def evidence_summary(features: list[dict[str, Any]], not_evaluated: list[dict[str, Any]]) -> list[str]:
    if not features:
        summary = ["No evaluated scorecard rules crossed their thresholds."]
    else:
        ordered = sorted(features, key=lambda item: (-int(item["points"]), item["name"]))
        summary = [str(feature["evidence"]) for feature in ordered[:5]]
    if not_evaluated:
        summary.append(
            f"{len(not_evaluated)} feature inputs were missing and were not scored as safe."
        )
    return summary


def recommended_next_steps(features: list[dict[str, Any]], not_evaluated: list[dict[str, Any]]) -> list[str]:
    domains = {str(feature["domain"]) for feature in features}
    steps: list[str] = []
    if "movement" in domains:
        steps.append("Review mover attribution for the same scope and confirm comparable current/baseline windows.")
    if "cache_busting" in domains:
        steps.append("Inspect query-string diversity, cache-key behavior, and cache miss concentration by host and path.")
    if "origin_impact" in domains:
        steps.append("Break down origin cost proxy by path, host, ASN, and bot class before changing origin-facing controls.")
    if "crawler_governance" in domains:
        steps.append("Check good crawler rate limits, 5xx exposure, robots.txt, llms.txt, and sitemap availability.")
    if "security_evidence" in domains:
        steps.append("Enrich with SIEM action, policy, auth-failure, and blocked-request summaries for the same entity.")
    if not steps and not_evaluated:
        steps.append("Regenerate aggregate rows with the missing scorecard feature inputs listed in not_evaluated_features.")
    if not steps:
        steps.append("Continue observing with summary-table aggregates and compare against the next baseline window.")
    return steps


def score_entity(
    row: dict[str, Any],
    entity_type: str,
    metadata: dict[str, Any],
    min_count: float = 100.0,
) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    not_evaluated: list[dict[str, Any]] = []
    for evaluator in FEATURE_EVALUATORS:
        feature, missing = evaluator(row)
        if feature is not None:
            features.append(feature)
        if missing is not None:
            not_evaluated.append(missing)

    domain_scores = {domain: 0 for domain in DOMAINS}
    for feature in features:
        domain = str(feature["domain"])
        domain_scores[domain] = domain_scores.get(domain, 0) + int(feature["points"])

    score = min(100, sum(int(feature["points"]) for feature in features))
    primary_domain = "none"
    nonzero_domains = [(domain, points) for domain, points in domain_scores.items() if points > 0]
    if nonzero_domains:
        primary_domain = sorted(nonzero_domains, key=lambda item: (-item[1], item[0]))[0][0]

    label, reasons = confidence(row, metadata, not_evaluated, min_count)
    scorecard = {
        "schema_version": SCORECARD_SCHEMA,
        "entity_type": entity_type,
        "entity": entity_value(row, entity_type),
        "scope": metadata.get("scope", {}),
        "comparison_type": metadata.get("comparison_type", "previous_window"),
        "granularity": metadata.get("granularity", ""),
        "table_used": metadata.get("table_used", ""),
        "score": score,
        "band": score_band(score),
        "primary_domain": primary_domain,
        "domain_scores": domain_scores,
        "features": sorted(features, key=lambda item: (str(item["domain"]), str(item["name"]))),
        "not_evaluated_features": sorted(not_evaluated, key=lambda item: (str(item["domain"]), str(item["name"]))),
        "evidence_summary": evidence_summary(features, not_evaluated),
        "recommended_next_steps": recommended_next_steps(features, not_evaluated),
        "confidence": label,
        "confidence_reasons": reasons,
        "interpretation_constraints": INTERPRETATION_CONSTRAINTS,
    }
    if "current_window" in metadata:
        scorecard["current_window"] = metadata["current_window"]
    if "baseline_windows" in metadata:
        scorecard["baseline_windows"] = metadata["baseline_windows"]
    return scorecard


def complete_contribution_scope(metadata: dict[str, Any]) -> bool:
    return metadata.get("rowset_complete") is True or metadata.get("contribution_basis") == "complete_scope"


def add_contribution_percentages(rows: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    if not complete_contribution_scope(metadata):
        return

    row_deltas: list[tuple[dict[str, Any], float, bool]] = []
    for row in rows:
        current, baseline = count_values(row)
        if current is None or baseline is None:
            continue
        has_contribution = first_number(row, ("contribution_pct", "contribution_to_total_delta_pct")) is not None
        row_deltas.append((row, current - baseline, has_contribution))

    basis = sum(abs(delta) for _, delta, _ in row_deltas)
    if basis <= 0:
        return
    for row, delta, has_contribution in row_deltas:
        if has_contribution:
            continue
        row["contribution_pct"] = abs(delta) / basis * 100.0


def build_index(scorecards: list[dict[str, Any]], metadata: dict[str, Any], limit: int = 0) -> dict[str, Any]:
    ranked = sorted(
        scorecards,
        key=lambda card: (-int(card["score"]), str(card["entity_type"]), str(card["entity"])),
    )
    if limit > 0:
        ranked = ranked[:limit]
    index = {
        "schema_version": INDEX_SCHEMA,
        "scope": metadata.get("scope", {}),
        "comparison_type": metadata.get("comparison_type", "previous_window"),
        "table_used": metadata.get("table_used", ""),
        "ranked_entities": [
            {
                "rank": index + 1,
                "entity_type": card["entity_type"],
                "entity": card["entity"],
                "score": card["score"],
                "band": card["band"],
                "primary_domain": card["primary_domain"],
                "confidence": card["confidence"],
            }
            for index, card in enumerate(ranked)
        ],
        "interpretation_constraints": INTERPRETATION_CONSTRAINTS,
    }
    if "current_window" in metadata:
        index["current_window"] = metadata["current_window"]
    if "baseline_windows" in metadata:
        index["baseline_windows"] = metadata["baseline_windows"]
    return index


def validate_advanced_scorecard_input_boundary(
    value: Any,
    *,
    scorecard_trusted_context: Any = None,
) -> None:
    if not isinstance(value, dict):
        return

    schema_version = value.get("schema_version")
    if schema_version == ADVANCED_ATTRIBUTION_SCHEMA:
        raise InvalidScorecardInputError(
            "advanced_attribution_report_not_scorecard_input",
            "Direct bot_attribution_report.v1 input is not accepted by scorecard.py.",
            details={"schema_version": schema_version},
        )

    if schema_version != ADVANCED_SCORECARD_INPUT_SCHEMA:
        return

    if scorecard_trusted_context is None:
        raise InvalidScorecardInputError(
            "scorecard_trusted_context_missing",
            "bot_scorecard_input.v1 requires an in-process trusted scorecard context.",
            details={
                "schema_version": schema_version,
                "scorecard_export_safe": value.get("scorecard_export_safe"),
            },
        )

    raise InvalidScorecardInputError(
        "scorecard_trusted_context_invalid",
        "bot_scorecard_input.v1 trusted handoff validation is not implemented in this package.",
        details={"schema_version": schema_version},
    )


def build_artifacts(
    value: Any,
    *,
    entity_type: str | None = None,
    min_count: float = 100.0,
    limit: int = 0,
    scorecard_trusted_context: Any = None,
) -> dict[str, Any]:
    validate_advanced_scorecard_input_boundary(
        value,
        scorecard_trusted_context=scorecard_trusted_context,
    )
    metadata = metadata_from(value)
    rows, inferred_entity_type = prepared_rows(value, entity_type)
    if inferred_entity_type not in SUPPORTED_ENTITY_TYPES:
        raise ValueError(
            "Input must include one of these entity columns: "
            + ", ".join(SUPPORTED_ENTITY_TYPES)
        )
    rows = [dict(row) for row in rows]
    add_contribution_percentages(rows, metadata)
    scorecards = [
        score_entity(row, inferred_entity_type, metadata, min_count)
        for row in rows
        if entity_value(row, inferred_entity_type) != ""
    ]
    scorecards = sorted(
        scorecards,
        key=lambda card: (-int(card["score"]), str(card["entity"])),
    )
    if limit > 0:
        scorecards = scorecards[:limit]
    index = build_index(scorecards, metadata, limit=limit)
    return {
        "schema_version": ARTIFACT_SCHEMA,
        "scorecards": scorecards,
        "index": index,
    }


def main() -> int:
    args = parse_args()
    try:
        value = json.loads(read_input(args))
        artifacts = build_artifacts(
            value,
            entity_type=args.entity_type,
            min_count=args.min_count,
            limit=args.limit,
        )
    except InvalidScorecardInputError as exc:
        print(json.dumps(exc.document, indent=2, sort_keys=True))
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.output == "scorecards":
        result: Any = artifacts["scorecards"]
    elif args.output == "index":
        result = artifacts["index"]
    else:
        result = artifacts
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
