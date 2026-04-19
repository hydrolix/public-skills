#!/usr/bin/env python3
"""Emit structured Bot Insights posture analytics from aggregate JSON.

This script does not query Hydrolix. Feed it Hydrolix MCP query results, saved
JSON, or pasted aggregate JSON that already contains current/baseline rows.
Hydrolix should do filtering, grouping, and aggregation; this script standardizes
report shape, confidence reasons, contribution math, and interpretation guards.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


POSTURE_SCHEMA = "bot_posture_movement.v1"
MOVER_SCHEMA = "bot_mover_attribution.v1"
CONTROL_SCHEMA = "bot_control_review.v1"

POSTURE_CONSTRAINTS = [
    "movement_only",
    "no_causal_claim",
    "llm_may_summarize_structured_evidence_only",
]
MOVER_CONSTRAINTS = [
    "attribution_from_aggregate_deltas",
    "no_causal_claim",
    "llm_may_summarize_structured_evidence_only",
]
CONTROL_CONSTRAINTS = [
    "control_effectiveness_review",
    "no_causal_claim_without_external_change_evidence",
    "llm_may_summarize_structured_evidence_only",
]

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
METADATA_KEYS = {
    "period",
    "timestamp",
    "time",
    "bucket",
    "window",
    "label",
    "dimension",
    "value",
    "current_count",
    "baseline_count",
    "before",
    "after",
    "expected",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute structured Bot Insights posture analytics from aggregate JSON."
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
        "--schema",
        choices=("auto", "posture", "movers", "control"),
        default="auto",
        help="Output schema to emit.",
    )
    parser.add_argument(
        "--min-count",
        type=float,
        default=100.0,
        help="Minimum current and baseline support count for high confidence.",
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


def first_number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in row:
            value = to_number(row[key])
            if value is not None:
                return value
    return None


def clean_number(value: float) -> float | int:
    rounded = round(value, 6)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def direction(delta: float) -> str:
    if delta > 0:
        return "increase"
    if delta < 0:
        return "decrease"
    return "no_change"


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


def rows_to_periods(value: Any) -> dict[str, dict[str, Any]]:
    if isinstance(value, dict):
        current = value.get("current")
        baseline = value.get("baseline")
        if isinstance(current, dict) and isinstance(baseline, dict):
            return {"current": current, "baseline": baseline}

    periods: dict[str, dict[str, Any]] = {}
    for row in result_rows(value):
        period = str(row.get("period", "")).lower()
        if period in {"current", "baseline", "before", "after"}:
            periods[period] = row

    if "current" in periods and "baseline" in periods:
        return {"current": periods["current"], "baseline": periods["baseline"]}
    if "after" in periods and "before" in periods:
        return {"current": periods["after"], "baseline": periods["before"]}
    raise ValueError(
        "Input must contain current/baseline objects or period rows with current/baseline."
    )


def metric_specs(value: dict[str, Any], current: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, Any]]:
    raw_specs = value.get("metrics")
    if isinstance(raw_specs, list) and raw_specs:
        specs: list[dict[str, Any]] = []
        for item in raw_specs:
            if isinstance(item, str):
                specs.append({"name": item})
            elif isinstance(item, dict) and "name" in item:
                specs.append(item)
        return specs

    names = sorted((set(current) & set(baseline)) - METADATA_KEYS)
    specs = []
    for name in names:
        if to_number(current.get(name)) is not None and to_number(baseline.get(name)) is not None:
            specs.append({"name": name})
    return specs


def comparison_granularity_matches(comparison_type: str, granularity: str) -> bool | None:
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
    if comparison_type in {"previous_window", "explicit_before_after", "post_change_vs_expected"}:
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
        summary_table_used = bool(table_used and table_used not in {"bot_detection", "bot_detection_siem"})
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


def metric_row(
    name: str,
    current: float,
    baseline: float,
    row_current: dict[str, Any],
    row_baseline: dict[str, Any],
    metadata: dict[str, Any],
    spec: dict[str, Any],
    min_count: float,
) -> dict[str, Any]:
    delta = current - baseline
    current_count, baseline_count = support_counts(
        name, current, baseline, row_current, row_baseline, metadata
    )
    label, reasons = confidence(
        table_used=str(metadata.get("table_used", "")),
        comparison_type=str(metadata.get("comparison_type", "")),
        granularity=str(metadata.get("granularity", "")),
        current_count=current_count,
        baseline_count=baseline_count,
        baseline_value=baseline,
        context=metadata,
        min_count=min_count,
    )
    row: dict[str, Any] = {
        "name": name,
        "current": clean_number(current),
        "baseline": clean_number(baseline),
        "absolute_delta": clean_number(delta),
        "pct_change": clean_number(pct_delta(current, baseline)),
        "direction": direction(delta),
        "confidence": label,
        "confidence_reasons": reasons,
    }
    if "unit" in spec:
        row["unit"] = spec["unit"]
    return row


def compare_posture(value: Any, min_count: float = 100.0) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    periods = rows_to_periods(value)
    current = periods["current"]
    baseline = periods["baseline"]
    metadata = dict(data.get("confidence_context", {}) if isinstance(data, dict) else {})
    for key in (
        "comparison_type",
        "granularity",
        "table_used",
        "scope",
        "current_window",
        "baseline_windows",
        "counts",
    ):
        if isinstance(data, dict) and key in data:
            metadata[key] = data[key]

    metrics: list[dict[str, Any]] = []
    for spec in metric_specs(data, current, baseline):
        name = str(spec["name"])
        current_value = to_number(current.get(name))
        baseline_value = to_number(baseline.get(name))
        if current_value is None or baseline_value is None:
            continue
        metrics.append(
            metric_row(
                name,
                current_value,
                baseline_value,
                current,
                baseline,
                metadata,
                spec,
                min_count,
            )
        )

    output: dict[str, Any] = {
        "schema_version": POSTURE_SCHEMA,
        "comparison_type": metadata.get("comparison_type", "previous_window"),
        "granularity": metadata.get("granularity", ""),
        "table_used": metadata.get("table_used", ""),
        "scope": metadata.get("scope", {}),
        "current_window": metadata.get("current_window", {}),
        "baseline_windows": metadata.get("baseline_windows", []),
        "metrics": metrics,
        "interpretation_constraints": POSTURE_CONSTRAINTS,
    }

    movers = data.get("movers") if isinstance(data, dict) else None
    if isinstance(movers, list):
        output["movers"] = compare_movers(value, min_count)["movers"]
    return output


def mover_value(row: dict[str, Any], dimension: str) -> Any:
    if "value" in row:
        return row["value"]
    if dimension in row:
        return row[dimension]
    return ""


def compare_movers(value: Any, min_count: float = 100.0) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    movers_input = data.get("movers") if isinstance(data, dict) else None
    if not isinstance(movers_input, list):
        movers_input = result_rows(value)

    metadata = dict(data.get("confidence_context", {}) if isinstance(data, dict) else {})
    dimension = str(data.get("dimension", "value")) if isinstance(data, dict) else "value"
    metric = str(data.get("metric", "requests")) if isinstance(data, dict) else "requests"
    table_used = str(data.get("table_used", "")) if isinstance(data, dict) else ""
    comparison_type = str(data.get("comparison_type", "previous_window")) if isinstance(data, dict) else "previous_window"
    granularity = str(data.get("granularity", "")) if isinstance(data, dict) else ""
    metadata.update(
        {
            "table_used": table_used,
            "comparison_type": comparison_type,
            "granularity": granularity,
        }
    )

    prepared: list[tuple[dict[str, Any], float, float, float]] = []
    for row in movers_input:
        if not isinstance(row, dict):
            continue
        current = to_number(row.get("current", row.get("current_requests")))
        baseline = to_number(row.get("baseline", row.get("baseline_requests")))
        if current is None or baseline is None:
            continue
        delta = current - baseline
        prepared.append((row, current, baseline, delta))

    total_delta = to_number(data.get("total_delta")) if isinstance(data, dict) else None
    total_delta_basis = "provided_total_delta"
    if total_delta is None:
        total_delta = sum(abs(delta) for _, _, _, delta in prepared)
        total_delta_basis = "sum_abs_mover_delta"

    movers: list[dict[str, Any]] = []
    for row, current, baseline, delta in prepared:
        basis = abs(total_delta) if total_delta else 0.0
        contribution = abs(delta) / basis * 100.0 if basis > 0 else 0.0
        current_count, baseline_count = support_counts(
            metric, current, baseline, {"requests": current}, {"requests": baseline}, metadata
        )
        label, reasons = confidence(
            table_used=table_used,
            comparison_type=comparison_type,
            granularity=granularity,
            current_count=current_count,
            baseline_count=baseline_count,
            baseline_value=baseline,
            context=metadata,
            min_count=min_count,
        )
        movers.append(
            {
                "dimension": dimension,
                "value": mover_value(row, dimension),
                "metric": metric,
                "current": clean_number(current),
                "baseline": clean_number(baseline),
                "absolute_delta": clean_number(delta),
                "pct_change": clean_number(pct_delta(current, baseline)),
                "direction": direction(delta),
                "contribution_pct": clean_number(contribution),
                "confidence": label,
                "confidence_reasons": reasons,
            }
        )

    return {
        "schema_version": MOVER_SCHEMA,
        "comparison_type": comparison_type,
        "granularity": granularity,
        "table_used": table_used,
        "dimension": dimension,
        "metric": metric,
        "total_delta": clean_number(total_delta or 0.0),
        "total_delta_basis": total_delta_basis,
        "movers": movers,
        "interpretation_constraints": MOVER_CONSTRAINTS,
    }


def control_status(
    after: float,
    expected: float,
    desired_direction: str | None = None,
    tolerance_pct: float = 5.0,
) -> str:
    delta = after - expected
    change_pct = abs(pct_delta(after, expected))
    if change_pct <= tolerance_pct:
        return "within_expected"
    actual = direction(delta)
    if desired_direction is None:
        return "increased" if actual == "increase" else "decreased"
    if actual == desired_direction:
        return "improved"
    return "review"


def control_metric_specs(data: dict[str, Any], after: dict[str, Any], expected: dict[str, Any]) -> list[dict[str, Any]]:
    raw_specs = data.get("target_metrics") or data.get("metrics")
    if isinstance(raw_specs, list) and raw_specs:
        specs: list[dict[str, Any]] = []
        for item in raw_specs:
            if isinstance(item, str):
                specs.append({"name": item})
            elif isinstance(item, dict) and "name" in item:
                specs.append(item)
        return specs
    return [{"name": name} for name in sorted((set(after) & set(expected)) - METADATA_KEYS)]


def compare_control(value: Any, min_count: float = 100.0) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Control review input must be a JSON object.")
    data = value

    before = data.get("before")
    after = data.get("after")
    expected = data.get("expected")
    if not isinstance(before, dict) or not isinstance(after, dict):
        periods = {}
        for row in result_rows(data):
            period = str(row.get("period", "")).lower()
            if period in {"before", "after"}:
                periods[period] = row
        before = periods.get("before", before)
        after = periods.get("after", after)
    if not isinstance(before, dict):
        before = {}
    if not isinstance(after, dict):
        raise ValueError("Control review input must contain after metrics.")
    if not isinstance(expected, dict):
        expected = before

    metadata = dict(data.get("confidence_context", {}) if isinstance(data.get("confidence_context"), dict) else {})
    for key in ("comparison_type", "granularity", "table_used", "counts"):
        if key in data:
            metadata[key] = data[key]
    metadata.setdefault("comparison_type", "post_change_vs_expected")

    desired = data.get("desired_directions", {})
    if not isinstance(desired, dict):
        desired = {}

    target_effects: list[dict[str, Any]] = []
    for spec in control_metric_specs(data, after, expected):
        metric = str(spec["name"])
        after_value = to_number(after.get(metric))
        expected_value = to_number(expected.get(metric))
        before_value = to_number(before.get(metric))
        if after_value is None or expected_value is None:
            continue
        delta = after_value - expected_value
        desired_direction = spec.get("desired_direction") or desired.get(metric)
        current_count, baseline_count = support_counts(
            metric,
            after_value,
            expected_value,
            after,
            expected,
            metadata,
        )
        label, reasons = confidence(
            table_used=str(metadata.get("table_used", "")),
            comparison_type="post_change_vs_expected",
            granularity=str(metadata.get("granularity", "")),
            current_count=current_count,
            baseline_count=baseline_count,
            baseline_value=expected_value,
            context=metadata,
            min_count=min_count,
        )
        target_effects.append(
            {
                "metric": metric,
                "before": clean_number(before_value) if before_value is not None else None,
                "after": clean_number(after_value),
                "expected": clean_number(expected_value),
                "absolute_delta_vs_expected": clean_number(delta),
                "pct_change_vs_expected": clean_number(pct_delta(after_value, expected_value)),
                "direction": direction(delta),
                "status": control_status(
                    after_value,
                    expected_value,
                    desired_direction=str(desired_direction) if desired_direction else None,
                    tolerance_pct=float(spec.get("tolerance_pct", data.get("tolerance_pct", 5.0))),
                ),
                "confidence": label,
                "confidence_reasons": reasons,
            }
        )

    return {
        "schema_version": CONTROL_SCHEMA,
        "comparison_type": "post_change_vs_expected",
        "change_time": data.get("change_time", ""),
        "target": data.get("target", {}),
        "table_used": metadata.get("table_used", ""),
        "target_effects": target_effects,
        "collateral_checks": data.get("collateral_checks", []),
        "displacement_checks": data.get("displacement_checks", []),
        "interpretation_constraints": CONTROL_CONSTRAINTS,
    }


def compare(value: Any, schema: str = "auto", min_count: float = 100.0) -> dict[str, Any]:
    selected = schema
    if selected == "auto":
        if isinstance(value, dict) and (
            value.get("comparison_type") == "post_change_vs_expected"
            or "expected" in value
            or "change_time" in value
        ):
            selected = "control"
        elif isinstance(value, dict) and "movers" in value and "current" not in value:
            selected = "movers"
        else:
            selected = "posture"

    if selected == "control":
        return compare_control(value, min_count)
    if selected == "movers":
        return compare_movers(value, min_count)
    return compare_posture(value, min_count)


def main() -> int:
    args = parse_args()
    try:
        value = json.loads(read_input(args))
        result = compare(value, schema=args.schema, min_count=args.min_count)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
