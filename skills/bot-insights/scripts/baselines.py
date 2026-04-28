#!/usr/bin/env python3
"""Shared deterministic baseline helpers for Bot Insights scripts."""

from __future__ import annotations

import math
from typing import Any


COUNT_METRICS = {
    "requests",
    "total_requests",
    "cnt_all",
    "current_requests",
    "baseline_requests",
    "siem_blocked_requests",
    "siem_auth_fail_requests",
    "siem_business_fail_requests",
}


def to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def first_number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in row:
            value = to_number(row[key])
            if value is not None:
                return value
    return None


def clean_number(value: float) -> float | int:
    if not math.isfinite(value):
        raise ValueError("Output numeric values must be finite.")
    rounded = round(value, 6)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def json_safe(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, int):
        return value
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    return value


def json_safe_metadata_value(
    metadata: dict[str, Any], key: str, default: Any
) -> Any:
    if key not in metadata:
        return default
    return json_safe(metadata[key])


def metadata_text(value: Any, default: str = "") -> str:
    safe_value = json_safe(value)
    if safe_value is None:
        return default
    return str(safe_value)


def direction(delta: float) -> str:
    if delta > 0:
        return "increase"
    if delta < 0:
        return "decrease"
    return "no_change"


def pct_delta(current: float, baseline: float) -> float:
    return (current - baseline) / max(baseline, 1.0) * 100.0


def comparison_granularity_matches(
    comparison_type: str, granularity: str
) -> bool | None:
    if not comparison_type or not granularity:
        return None
    day_methods = {
        "quarter_over_quarter",
        "month_over_month",
        "year_over_year",
        "same_week_last_year",
    }
    hour_methods = {"same_weekday_hour_last_week", "same_hour_yesterday"}
    if comparison_type in day_methods:
        return granularity == "day"
    if comparison_type in hour_methods:
        return granularity == "hour"
    if comparison_type == "week_over_week":
        return granularity in {"day", "hour"}
    if comparison_type in {
        "previous_window",
        "explicit_before_after",
        "post_change_vs_expected",
    }:
        return granularity in {"minute", "hour", "day", ""}
    return None


def support_counts(
    metric: str,
    current: float,
    baseline: float,
    row_current: dict[str, Any],
    row_baseline: dict[str, Any],
    context: dict[str, Any],
) -> tuple[float | None, float | None]:
    counts = context.get("counts")
    if isinstance(counts, dict):
        current_count = to_number(counts.get("current"))
        baseline_count = to_number(counts.get("baseline"))
        if current_count is not None or baseline_count is not None:
            return current_count, baseline_count

    current_count = first_number(row_current, "current_count")
    baseline_count = first_number(row_current, "baseline_count")
    if baseline_count is None:
        baseline_count = first_number(row_baseline, "baseline_count")
    if current_count is not None or baseline_count is not None:
        return current_count, baseline_count

    current_count = first_number(row_current, "requests", "cnt_all")
    baseline_count = first_number(row_baseline, "requests", "cnt_all")
    if metric in COUNT_METRICS:
        current_count = current
        baseline_count = baseline
    return current_count, baseline_count


def confidence(
    *,
    table_used: str,
    comparison_type: str,
    granularity: str,
    current_count: float | None,
    baseline_count: float | None,
    baseline_value: float,
    context: dict[str, Any],
    min_count: float,
) -> tuple[str, list[str]]:
    reasons: list[str] = []

    summary_table_used = context.get("summary_table_used")
    if summary_table_used is None:
        summary_table_used = bool(
            table_used and table_used not in {"bot_detection", "bot_detection_siem"}
        )
    if summary_table_used:
        reasons.append("summary_table_used")
    else:
        reasons.append("raw_table_fallback")

    if context.get("missing_retained_dimension"):
        reasons.append("missing_retained_dimension")
    elif summary_table_used:
        reasons.append("retained_dimensions_fit")

    comparable = context.get("comparable_windows")
    if comparable is None:
        comparable = True
    if comparable:
        reasons.append("comparable_windows_available")
    if context.get("fallback_baseline_selected"):
        reasons.append("fallback_baseline_selected")

    granularity_match = comparison_granularity_matches(comparison_type, granularity)
    if granularity_match is True:
        reasons.append("granularity_matches_comparison")
    elif granularity_match is False:
        reasons.append("granularity_mismatch")

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

    if baseline_value < 1:
        reasons.append("zero_baseline_guard")
    if context.get("partial_current_bucket"):
        reasons.append("partial_current_bucket")
    if context.get("source_coverage_caveat") or context.get("source_caveats"):
        reasons.append("source_coverage_caveat")

    low_reasons = {
        "raw_table_fallback",
        "missing_retained_dimension",
        "granularity_mismatch",
        "sparse_counts",
        "partial_current_bucket",
    }
    if any(reason in reasons for reason in low_reasons) or not comparable:
        label = "low"
    elif "fallback_baseline_selected" in reasons or "source_coverage_caveat" in reasons:
        label = "medium"
    else:
        label = "high"

    return label, reasons
