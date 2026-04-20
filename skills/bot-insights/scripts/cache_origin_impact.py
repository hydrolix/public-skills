#!/usr/bin/env python3
"""Build cache-busting origin-impact reports from aggregate inputs.

This module parses and validates aggregate rows, derives canonical current,
baseline, and delta metrics, then assembles scored detector candidates.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPORT_SCHEMA = "cache_origin_impact_report.v1"
ANALYSIS_TYPE = "cache_busting_origin_impact"

SUPPORTED_ROW_DIMENSION_SETS = (
    ("request_path_norm",),
    ("request_path_norm", "bot_class"),
    ("request_path_norm", "asn_type"),
    ("request_path_norm", "bot_class", "asn_type"),
)
ACCEPTED_HOST_CONTEXT_FORMS = (
    "scope.request_host",
    "row_level_request_host",
)
INTERPRETATION_CONSTRAINTS = [
    "mechanical_candidate_only",
    "no_causal_claim",
    "origin_pressure_score_is_proxy",
    "not_a_billing_or_capacity_unit",
    "llm_may_summarize_structured_evidence_only",
]

SUPPORTED_DIMENSIONS = {"request_host", "request_path_norm", "bot_class", "asn_type"}
SUPPORTED_DIMENSION_SET_KEYS = {
    frozenset(dimensions) for dimensions in SUPPORTED_ROW_DIMENSION_SETS
}
PERIOD_VALUES = {"current", "baseline", "after", "before"}
METADATA_KEYS = {
    "period",
    "timestamp",
    "time",
    "bucket",
    "window",
    "label",
}
DIMENSION_KEYS = {"request_host", "request_path_norm", "bot_class", "asn_type"}

CANONICAL_ALIASES = {
    "requests": ("requests", "total_requests", "cnt_all"),
    "cache_misses": ("cache_misses", "cnt_cache_miss"),
    "unique_query_strings": ("unique_query_strings", "uniq_qs"),
    "origin_p95_ms": (
        "origin_p95_ms",
        "p95_origin_ttfb",
        "p95_origin_ttfb_ms",
        "origin_ttfb_p95_ms",
    ),
    "origin_p99_ms": (
        "origin_p99_ms",
        "p99_origin_ttfb",
        "p99_origin_ttfb_ms",
        "origin_ttfb_p99_ms",
    ),
    "response_bytes": ("response_bytes", "response_total_bytes"),
}
ALIAS_TO_CANONICAL = {
    alias: canonical
    for canonical, aliases in CANONICAL_ALIASES.items()
    for alias in aliases
}
ADDITIVE_BASELINE_METRICS = {"requests", "cache_misses", "response_bytes"}
SUFFICIENT_REQUEST_COUNT = 1000
SUFFICIENT_CACHE_MISS_COUNT = 100
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
LOW_CONFIDENCE_REASONS = {
    "sparse_counts",
    "missing_retained_dimension",
    "contribution_withheld_source_limited",
    "partial_current_bucket",
}
LOW_CONFIDENCE_LIMITATIONS = {
    "missing_baseline",
    "broad_raw_table_fallback",
    "source_limited_rowset",
}
MEDIUM_ONLY_CONFIDENCE_REASONS = {
    "caller_supplied_json_confidence_cap",
    "query_string_cardinality_approximate",
    "origin_latency_worst_bucket",
}
PERIOD_ALIASES = {
    "current": "current",
    "after": "current",
    "baseline": "baseline",
    "before": "baseline",
}
DERIVED_METRIC_INPUTS = {
    "current_miss_rate_pct": ("current", ("requests", "cache_misses")),
    "baseline_miss_rate_pct": ("baseline", ("requests", "cache_misses")),
    "current_qs_diversity_ratio": (
        "current",
        ("requests", "unique_query_strings"),
    ),
    "baseline_qs_diversity_ratio": (
        "baseline",
        ("requests", "unique_query_strings"),
    ),
    "current_origin_pressure_score": (
        "current",
        ("cache_misses", "origin_p95_ms"),
    ),
    "baseline_origin_pressure_score": (
        "baseline",
        ("cache_misses", "origin_p95_ms"),
    ),
    "request_delta": ("delta", ("current.requests", "baseline.requests")),
    "cache_miss_delta": (
        "delta",
        ("current.cache_misses", "baseline.cache_misses"),
    ),
    "miss_rate_delta_pp": (
        "delta",
        ("current.miss_rate_pct", "baseline.miss_rate_pct"),
    ),
    "qs_diversity_delta": (
        "delta",
        ("current.qs_diversity_ratio", "baseline.qs_diversity_ratio"),
    ),
    "origin_p95_delta_ms": (
        "delta",
        ("current.origin_p95_ms", "baseline.origin_p95_ms"),
    ),
    "origin_p99_delta_ms": (
        "delta",
        ("current.origin_p99_ms", "baseline.origin_p99_ms"),
    ),
    "cache_miss_pct_change": (
        "delta",
        ("current.cache_misses", "baseline.cache_misses"),
    ),
    "origin_p95_pct_change": (
        "delta",
        ("current.origin_p95_ms", "baseline.origin_p95_ms"),
    ),
    "origin_pressure_delta": (
        "delta",
        (
            "current.origin_pressure_score",
            "baseline.origin_pressure_score",
        ),
    ),
}
COMPLETE_SCOPE_BASIS_VALUES = {
    "complete_scope",
    "complete_scope_pre_limit",
}
SOURCE_LIMITED_BASIS_VALUES = {
    "source_limited",
    "limited_source_rows",
    "post_limit",
}
SCORING_THRESHOLDS = {
    "high_query_string_diversity": 0.8,
    "moderate_query_string_diversity": 0.5,
    "query_string_diversity_increased": 0.25,
    "high_miss_rate": 80.0,
    "miss_rate_increased": 10.0,
    "origin_tail_latency_delta_ms": 100.0,
    "origin_tail_latency_pct_change": 50.0,
    "origin_pressure_contributor": 10.0,
    "bot_attributable_majority": 50.0,
    "large_current_volume": 10000.0,
}
SEMANTIC_REQUIREMENT_KEYS = {
    "unique_query_strings": (
        "unique_query_strings",
        "query_string_cardinality",
        "uniq_qs",
    ),
    "origin_p95_ms": (
        "origin_p95_ms",
        "origin_latency",
        "origin_percentiles",
        "p95_origin_ttfb",
    ),
    "origin_p99_ms": (
        "origin_p99_ms",
        "origin_latency",
        "origin_percentiles",
        "p99_origin_ttfb",
    ),
    "contribution_fields": (
        "contribution_fields",
        "cache_miss_contribution_pct",
        "origin_pressure_contribution_pct",
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute cache-busting origin-impact reports from aggregate JSON."
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
        "--limit",
        type=int,
        help="Optional maximum number of ranked candidates to emit.",
    )
    return parser.parse_args()


def read_input(args: argparse.Namespace) -> str:
    if args.file:
        return args.file.read_text(encoding="utf-8")
    if args.text:
        return " ".join(args.text)
    return sys.stdin.read()


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
            converted.append(
                {name: row[index] for index, name in enumerate(names) if index < len(row)}
            )
    return converted


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


def clean_number(value: float | int | None) -> float | int | None:
    if value is None:
        return None
    rounded = round(float(value), 6)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def pct_delta(current: float, baseline: float) -> float:
    return (current - baseline) / max(baseline, 1.0) * 100.0


def _require_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Input must be a JSON object containing aggregate rows.")
    return value


def _validate_metric_or_analysis_type(value: dict[str, Any]) -> None:
    analysis_type = value.get("analysis_type")
    metric = value.get("metric")
    if not analysis_type and not metric:
        raise ValueError("Input must include metric or analysis_type.")
    if analysis_type and analysis_type != ANALYSIS_TYPE:
        raise ValueError(
            f"Unsupported analysis_type {analysis_type!r}; expected {ANALYSIS_TYPE!r}."
        )


def _validate_current_window(value: dict[str, Any]) -> dict[str, Any]:
    current_window = value.get("current_window")
    if not isinstance(current_window, dict):
        raise ValueError("current_window is required and must include start and end.")
    if not current_window.get("start") or not current_window.get("end"):
        raise ValueError("current_window is malformed; start and end are required.")
    if _window_duration_seconds(current_window) is None:
        raise ValueError(
            "current_window is malformed; start and end must be valid timestamps with end after start."
        )
    return current_window


def _validate_dimensions(value: dict[str, Any]) -> list[str]:
    dimensions = value.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise ValueError("dimensions is required and must be a non-empty list.")
    if not all(isinstance(dimension, str) and dimension for dimension in dimensions):
        raise ValueError("dimensions must contain non-empty string names.")

    unsupported = sorted(set(dimensions) - SUPPORTED_DIMENSIONS)
    if unsupported:
        raise ValueError(
            "Unsupported v1 dimension(s): "
            + ", ".join(unsupported)
            + ". Supported path-grain dimensions are request_path_norm, bot_class, and asn_type."
        )

    row_dimensions = [dimension for dimension in dimensions if dimension != "request_host"]
    if frozenset(row_dimensions) not in SUPPORTED_DIMENSION_SET_KEYS:
        supported = [
            " + ".join(dimensions)
            for dimensions in SUPPORTED_ROW_DIMENSION_SETS
        ]
        raise ValueError(
            "Unsupported dimensions for v1 path-grain detector; supported row-level "
            "dimension sets are: "
            + "; ".join(supported)
            + "."
        )
    return dimensions


def _validated_rows(value: dict[str, Any]) -> list[dict[str, Any]]:
    raw_rows = value.get("rows")
    if raw_rows is None:
        raw_rows = value.get("data")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("rows is required and must contain at least one row.")

    if all(isinstance(row, dict) for row in raw_rows):
        return list(raw_rows)

    if all(isinstance(row, list) for row in raw_rows):
        columns = value.get("columns")
        if not isinstance(columns, list) or not columns:
            raise ValueError("MCP-style list rows require a non-empty columns list.")
        names = column_names(columns)
        return [
            {name: row[index] for index, name in enumerate(names) if index < len(row)}
            for row in raw_rows
        ]

    raise ValueError(
        "rows must contain either dictionaries or lists with columns; mixed row containers are unsupported."
    )


def _is_blank(value: Any) -> bool:
    return value is None or value == ""


def _validate_host_context(
    value: dict[str, Any],
    dimensions: list[str],
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    scope = value.get("scope") if isinstance(value.get("scope"), dict) else {}
    scoped_host = scope.get("request_host")
    if isinstance(scoped_host, str) and scoped_host:
        conflicting_host_rows = [
            index + 1
            for index, row in enumerate(rows)
            if not _is_blank(row.get("request_host"))
            and str(row.get("request_host")) != scoped_host
        ]
        if conflicting_host_rows:
            raise ValueError(
                "Row-level request_host values must match scope.request_host "
                "when scoped host context is supplied."
            )
        return scope, True

    missing_host_rows = [
        index + 1
        for index, row in enumerate(rows)
        if _is_blank(row.get("request_host"))
    ]
    if missing_host_rows:
        raise ValueError(
            "Host context is required: provide scope.request_host or request_host on every row."
        )
    return scope, False


def _row_has_period(row: dict[str, Any]) -> bool:
    return str(row.get("period", "")).lower() in PERIOD_VALUES


def _row_has_combined_metrics(row: dict[str, Any]) -> bool:
    return any(
        key.startswith(("current_", "baseline_", "current.", "baseline."))
        or key.endswith(("_current", "_baseline"))
        for key in row
    )


def _validate_row_shape(rows: list[dict[str, Any]]) -> None:
    saw_period = any(_row_has_period(row) for row in rows)
    saw_combined_or_unlabeled = any(
        not _row_has_period(row) or _row_has_combined_metrics(row) for row in rows
    )
    if saw_period and saw_combined_or_unlabeled:
        raise ValueError(
            "Input rows must not mix period-split rows with already-combined candidate rows."
        )


def _validate_dimension_values(
    dimensions: list[str],
    rows: list[dict[str, Any]],
    *,
    scoped_host: bool,
) -> None:
    required_dimensions = set(dimensions)
    if scoped_host:
        required_dimensions.discard("request_host")

    for index, row in enumerate(rows, start=1):
        missing = sorted(
            dimension for dimension in required_dimensions if _is_blank(row.get(dimension))
        )
        if missing:
            raise ValueError(
                f"Row {index} is missing dimension value(s): " + ", ".join(missing) + "."
            )


def _split_period_key(key: str) -> tuple[str, str]:
    for prefix in ("current_", "baseline_"):
        if key.startswith(prefix):
            return prefix[:-1], key[len(prefix):]
    for prefix in ("current.", "baseline."):
        if key.startswith(prefix):
            return prefix[:-1], key[len(prefix):]
    for suffix in ("_current", "_baseline"):
        if key.endswith(suffix):
            return suffix[1:], key[: -len(suffix)]
    return "row", key


def _canonical_for_key(key: str) -> tuple[str, str] | None:
    period, base_key = _split_period_key(key)
    canonical = ALIAS_TO_CANONICAL.get(base_key)
    if canonical is None:
        return None
    return period, canonical


def _is_numeric_field(key: str) -> bool:
    if key in METADATA_KEYS or key in DIMENSION_KEYS:
        return False
    canonical = _canonical_for_key(key)
    if canonical is not None:
        return True
    _, base_key = _split_period_key(key)
    if base_key in METADATA_KEYS or base_key in DIMENSION_KEYS:
        return False
    return (
        base_key.endswith("_pct")
        or base_key.endswith("_ratio")
        or base_key.endswith("_ms")
        or base_key.endswith("_bytes")
        or base_key.endswith("_count")
        or base_key.endswith("_requests")
        or base_key.endswith("_misses")
        or base_key.endswith("_score")
        or "_requests_for_" in base_key
        or "_misses_for_" in base_key
        or "origin_pressure" in base_key
        or "cache_miss" in base_key
        or base_key.startswith("cnt_")
        or base_key.startswith("uniq_")
    )


def _is_count_field(key: str) -> bool:
    canonical = _canonical_for_key(key)
    if canonical is not None and canonical[1] in {
        "requests",
        "cache_misses",
        "unique_query_strings",
        "response_bytes",
    }:
        return True
    _, base_key = _split_period_key(key)
    if _is_percentage_field(key):
        return False
    return (
        base_key.endswith("_count")
        or base_key.endswith("_requests")
        or base_key.endswith("_misses")
        or "_requests_for_" in base_key
        or "_misses_for_" in base_key
        or base_key.startswith("cnt_")
        or base_key.startswith("uniq_")
    )


def _is_percentage_field(key: str) -> bool:
    _, base_key = _split_period_key(key)
    return base_key.endswith("_pct")


def _validate_numeric_fields(rows: list[dict[str, Any]]) -> None:
    for row_index, row in enumerate(rows, start=1):
        for key, value in row.items():
            if not _is_numeric_field(key):
                continue
            number = to_number(value)
            if number is None:
                raise ValueError(f"Row {row_index} field {key!r} must be numeric.")
            if _is_count_field(key) and number < 0:
                raise ValueError(f"Row {row_index} field {key!r} must not be negative.")
            if _is_percentage_field(key) and not 0 <= number <= 100:
                raise ValueError(
                    f"Row {row_index} field {key!r} must be a percentage from 0 to 100."
                )


def _validate_alias_conflicts(rows: list[dict[str, Any]]) -> None:
    for row_index, row in enumerate(rows, start=1):
        grouped: dict[tuple[str, str], list[tuple[str, float]]] = {}
        for key, value in row.items():
            canonical = _canonical_for_key(key)
            if canonical is None:
                continue
            number = to_number(value)
            if number is None:
                continue
            grouped.setdefault(canonical, []).append((key, number))

        for (_period, canonical), values in grouped.items():
            if len(values) < 2:
                continue
            first_key, first_value = values[0]
            for key, value in values[1:]:
                if value != first_value:
                    raise ValueError(
                        f"Row {row_index} has conflicting aliases for {canonical}: "
                        f"{first_key}={clean_number(first_value)} and {key}={clean_number(value)}."
                    )


def _semantic_requirements_for_key(key: str) -> set[str]:
    requirements: set[str] = set()
    canonical = _canonical_for_key(key)
    if canonical is not None:
        _period, canonical_name = canonical
        if canonical_name == "unique_query_strings":
            requirements.add("unique_query_strings")
        elif canonical_name in {"origin_p95_ms", "origin_p99_ms"}:
            requirements.add(canonical_name)

    _, base_key = _split_period_key(key)
    if base_key.endswith("contribution_pct") or "_for_contribution" in base_key:
        requirements.add("contribution_fields")
    return requirements


def _semantics_satisfy(
    metric_semantics: dict[str, Any],
    requirement: str,
) -> bool:
    return any(
        key in metric_semantics
        for key in SEMANTIC_REQUIREMENT_KEYS.get(requirement, (requirement,))
    )


def _validate_metric_semantics(value: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    requirements: set[str] = set()
    for row in rows:
        for key in row:
            requirements.update(_semantic_requirements_for_key(key))

    metric_semantics = value.get("metric_semantics")
    if not requirements:
        return metric_semantics if isinstance(metric_semantics, dict) else {}

    if not isinstance(metric_semantics, dict) or not metric_semantics:
        raise ValueError(
            "metric_semantics is required when rows include query-string cardinality, "
            "origin percentile, or precomputed contribution fields."
        )

    missing = sorted(
        requirement
        for requirement in requirements
        if not _semantics_satisfy(metric_semantics, requirement)
    )
    if missing:
        raise ValueError(
            "metric_semantics is missing required entry for: "
            + ", ".join(missing)
            + "."
        )
    return metric_semantics


def _validate_rows(
    value: dict[str, Any],
    dimensions: list[str],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    _validate_row_shape(rows)
    scope, scoped_host = _validate_host_context(value, dimensions, rows)
    _validate_dimension_values(dimensions, rows, scoped_host=scoped_host)
    _validate_numeric_fields(rows)
    _validate_alias_conflicts(rows)
    return scope


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _window_duration_seconds(window: Any) -> float | None:
    if not isinstance(window, dict):
        return None
    start = _parse_timestamp(window.get("start"))
    end = _parse_timestamp(window.get("end"))
    if start is None or end is None:
        return None
    duration = (end - start).total_seconds()
    if duration <= 0:
        return None
    return duration


def _baseline_duration_seconds(value: dict[str, Any]) -> float | None:
    baseline_windows = value.get("baseline_windows")
    if isinstance(baseline_windows, dict):
        baseline_windows = [baseline_windows]
    if not isinstance(baseline_windows, list) or not baseline_windows:
        return None

    total = 0.0
    for window in baseline_windows:
        duration = _window_duration_seconds(window)
        if duration is not None:
            total += duration
    return total if total > 0 else None


def _baseline_normalization(
    value: dict[str, Any],
    current_window: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    current_duration = _window_duration_seconds(current_window)
    baseline_duration = _baseline_duration_seconds(value)
    if current_duration is None or baseline_duration is None:
        return {
            "method": "missing_or_current_only",
            "factor": None,
            "applies_to": [],
        }

    factor = current_duration / baseline_duration
    if abs(factor - 1.0) < 0.000001:
        return {
            "method": "none_equal_duration_windows",
            "factor": 1.0,
            "applies_to": [],
        }

    affected = sorted(_baseline_additive_metrics(rows))
    return {
        "method": "duration_normalized_additive_metrics",
        "factor": clean_number(factor),
        "applies_to": affected,
    }


def _baseline_additive_metrics(rows: list[dict[str, Any]]) -> set[str]:
    affected: set[str] = set()
    for row in rows:
        row_period = PERIOD_ALIASES.get(str(row.get("period", "")).lower())
        for key in row:
            canonical = _canonical_for_key(key)
            if canonical is None:
                continue
            period, canonical_name = canonical
            if canonical_name not in ADDITIVE_BASELINE_METRICS:
                continue
            if period == "baseline" or (period == "row" and row_period == "baseline"):
                affected.add(canonical_name)
    return affected


def _entity_from_row(
    row: dict[str, Any],
    dimensions: list[str],
    scope: dict[str, Any],
) -> dict[str, Any]:
    entity: dict[str, Any] = {}
    scoped_host = scope.get("request_host")
    if _is_blank(scoped_host) and not _is_blank(row.get("request_host")):
        entity["request_host"] = row["request_host"]

    for dimension in dimensions:
        if dimension == "request_host" and not _is_blank(scoped_host):
            continue
        value = row.get(dimension)
        if not _is_blank(value):
            entity[dimension] = value
    return entity


def _entity_key(entity: dict[str, Any], dimensions: list[str]) -> tuple[Any, ...]:
    key_dimensions = list(dimensions)
    if "request_host" in entity and "request_host" not in key_dimensions:
        key_dimensions = ["request_host", *key_dimensions]
    return tuple(entity.get(dimension) for dimension in key_dimensions)


def _collect_metrics(
    row: dict[str, Any],
    *,
    period_override: str | None = None,
) -> dict[str, dict[str, float]]:
    periods: dict[str, dict[str, float]] = {"current": {}, "baseline": {}}
    for key, value in row.items():
        canonical = _canonical_for_key(key)
        if canonical is None:
            continue
        period, canonical_name = canonical
        normalized_period = period_override or PERIOD_ALIASES.get(period)
        if normalized_period is None and period == "row":
            normalized_period = "current"
        if normalized_period not in periods:
            continue
        number = to_number(value)
        if number is not None:
            periods[normalized_period][canonical_name] = number
    return periods


def _metric_rows(
    rows: list[dict[str, Any]],
    dimensions: list[str],
    scope: dict[str, Any],
) -> list[dict[str, Any]]:
    if not any(_row_has_period(row) for row in rows):
        return [
            {
                "entity": _entity_from_row(row, dimensions, scope),
                "source_row": row,
                **_collect_metrics(row),
            }
            for row in rows
        ]

    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for row in rows:
        row_period = PERIOD_ALIASES.get(str(row.get("period", "")).lower())
        if row_period is None:
            continue
        entity = _entity_from_row(row, dimensions, scope)
        key = _entity_key(entity, dimensions)
        if key not in grouped:
            grouped[key] = {
                "entity": entity,
                "source_row": {},
                "current": {},
                "baseline": {},
            }
            order.append(key)
        if row_period == "current":
            grouped[key]["source_row"] = row
        elif not grouped[key]["source_row"]:
            grouped[key]["source_row"] = row
        collected = _collect_metrics(row, period_override=row_period)
        grouped[key][row_period].update(collected[row_period])
    return [grouped[key] for key in order]


def _ratio(numerator: float | None, denominator: float | None, multiplier: float = 1.0) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator * multiplier


def _qs_ratio(
    metrics: dict[str, float],
    *,
    exact_period_unique: bool,
) -> float | None:
    ratio = _ratio(metrics.get("unique_query_strings"), metrics.get("requests"))
    if ratio is None:
        return None
    if exact_period_unique:
        return min(max(ratio, 0.0), 1.0)
    return ratio


def _origin_pressure(metrics: dict[str, float]) -> float | None:
    cache_misses = metrics.get("cache_misses")
    origin_p95_ms = metrics.get("origin_p95_ms")
    if cache_misses is None or origin_p95_ms is None:
        return None
    return cache_misses * max(origin_p95_ms, 1.0) / 1000.0


def _derive_period_metrics(
    metrics: dict[str, float],
    metric_semantics: dict[str, Any],
) -> tuple[dict[str, float], list[str]]:
    derived = dict(metrics)
    reasons: list[str] = []

    miss_rate = _ratio(derived.get("cache_misses"), derived.get("requests"), 100.0)
    if miss_rate is not None:
        derived["miss_rate_pct"] = miss_rate

    if "unique_query_strings" in derived:
        unique_semantics = _semantic_basis(
            metric_semantics,
            *SEMANTIC_REQUIREMENT_KEYS["unique_query_strings"],
        )
        exact_period_unique = unique_semantics == "exact_period_unique"
        qs_ratio = _qs_ratio(derived, exact_period_unique=exact_period_unique)
        if qs_ratio is not None:
            derived["qs_diversity_ratio"] = qs_ratio
        if exact_period_unique:
            reasons.append("query_string_cardinality_exact")
        else:
            reasons.append("query_string_cardinality_approximate")

    if "origin_p95_ms" in derived or "origin_p99_ms" in derived:
        latency_semantics = _semantic_basis(
            metric_semantics,
            *SEMANTIC_REQUIREMENT_KEYS["origin_p95_ms"],
            *SEMANTIC_REQUIREMENT_KEYS["origin_p99_ms"],
        )
        latency_semantics_text = str(latency_semantics or "").lower()
        if latency_semantics_text in {
            "metadata_merged_quantile",
            "merged_quantile",
            "exact_merge",
            "merge_exact",
        }:
            reasons.append("origin_latency_merge_exact")
        elif "worst" in latency_semantics_text or "bucket" in latency_semantics_text:
            reasons.append("origin_latency_worst_bucket")

    origin_pressure = _origin_pressure(derived)
    if origin_pressure is not None:
        derived["origin_pressure_score"] = origin_pressure

    return derived, reasons


def _normalize_baseline_metrics(
    metrics: dict[str, float],
    normalization: dict[str, Any],
) -> dict[str, float]:
    normalized = dict(metrics)
    if normalization.get("method") != "duration_normalized_additive_metrics":
        return normalized
    factor = to_number(normalization.get("factor"))
    if factor is None:
        return normalized
    for metric in ADDITIVE_BASELINE_METRICS:
        if metric in normalized:
            normalized[metric] *= factor
    return normalized


def _value_at_path(
    current: dict[str, float],
    baseline: dict[str, float],
    path: str,
) -> float | None:
    container_name, metric = path.split(".", 1)
    container = current if container_name == "current" else baseline
    return container.get(metric)


def _delta_metrics(
    current: dict[str, float],
    baseline: dict[str, float],
) -> dict[str, float]:
    deltas: dict[str, float] = {}
    delta_pairs = {
        "requests": ("requests", "requests"),
        "cache_misses": ("cache_misses", "cache_misses"),
        "miss_rate_delta_pp": ("miss_rate_pct", "miss_rate_pct"),
        "qs_diversity_delta": ("qs_diversity_ratio", "qs_diversity_ratio"),
        "origin_p95_delta_ms": ("origin_p95_ms", "origin_p95_ms"),
        "origin_p99_delta_ms": ("origin_p99_ms", "origin_p99_ms"),
        "origin_pressure_delta": (
            "origin_pressure_score",
            "origin_pressure_score",
        ),
    }
    for output_name, (current_name, baseline_name) in delta_pairs.items():
        current_value = current.get(current_name)
        baseline_value = baseline.get(baseline_name)
        if current_value is not None and baseline_value is not None:
            deltas[output_name] = current_value - baseline_value

    cache_misses = current.get("cache_misses")
    baseline_cache_misses = baseline.get("cache_misses")
    if cache_misses is not None and baseline_cache_misses is not None:
        deltas["cache_miss_pct_change"] = pct_delta(cache_misses, baseline_cache_misses)

    origin_p95 = current.get("origin_p95_ms")
    baseline_origin_p95 = baseline.get("origin_p95_ms")
    if origin_p95 is not None and baseline_origin_p95 is not None:
        deltas["origin_p95_pct_change"] = pct_delta(origin_p95, baseline_origin_p95)
    return deltas


def _clean_metric_map(metrics: dict[str, float]) -> dict[str, float | int]:
    return {
        key: clean_number(value)
        for key, value in metrics.items()
        if value is not None
    }


def _missing_inputs(
    current: dict[str, float],
    baseline: dict[str, float],
    inputs: tuple[str, ...],
    *,
    default_scope: str,
) -> list[str]:
    missing: list[str] = []
    for input_name in inputs:
        if "." in input_name:
            if _value_at_path(current, baseline, input_name) is None:
                missing.append(input_name)
        elif default_scope == "baseline":
            if input_name not in baseline:
                missing.append(f"baseline.{input_name}")
        elif input_name not in current:
            missing.append(f"current.{input_name}")
    return missing


def _not_evaluated_entries(
    entity: dict[str, Any],
    current: dict[str, float],
    baseline: dict[str, float],
    deltas: dict[str, float],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    available = {
        "current_miss_rate_pct": current.get("miss_rate_pct"),
        "baseline_miss_rate_pct": baseline.get("miss_rate_pct"),
        "current_qs_diversity_ratio": current.get("qs_diversity_ratio"),
        "baseline_qs_diversity_ratio": baseline.get("qs_diversity_ratio"),
        "current_origin_pressure_score": current.get("origin_pressure_score"),
        "baseline_origin_pressure_score": baseline.get("origin_pressure_score"),
        "request_delta": deltas.get("requests"),
        "cache_miss_delta": deltas.get("cache_misses"),
        "miss_rate_delta_pp": deltas.get("miss_rate_delta_pp"),
        "qs_diversity_delta": deltas.get("qs_diversity_delta"),
        "origin_p95_delta_ms": deltas.get("origin_p95_delta_ms"),
        "origin_p99_delta_ms": deltas.get("origin_p99_delta_ms"),
        "cache_miss_pct_change": deltas.get("cache_miss_pct_change"),
        "origin_p95_pct_change": deltas.get("origin_p95_pct_change"),
        "origin_pressure_delta": deltas.get("origin_pressure_delta"),
    }
    for name, (default_scope, inputs) in DERIVED_METRIC_INPUTS.items():
        if available.get(name) is not None:
            continue
        missing_inputs = _missing_inputs(
            current,
            baseline,
            inputs,
            default_scope=default_scope,
        )
        entries.append(
            {
                "entity": entity,
                "name": name,
                "reason": "missing_optional_metric_input"
                if missing_inputs
                else "not_computable_from_supplied_inputs",
                "missing_inputs": missing_inputs,
            }
        )
    return entries


def _row_number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        number = to_number(row.get(key))
        if number is not None:
            return number
    return None


def _pct_from_parts(numerator: float | None, denominator: float | None) -> float | None:
    return _ratio(numerator, denominator, 100.0)


def _semantic_basis(metric_semantics: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in metric_semantics:
            return metric_semantics[key]
    return None


def _contribution_basis(
    data: dict[str, Any],
    metric_semantics: dict[str, Any],
) -> str | None:
    basis = data.get("contribution_basis")
    if basis is None:
        basis = _semantic_basis(
            metric_semantics,
            "contribution_fields",
            "cache_miss_contribution_pct",
            "origin_pressure_contribution_pct",
        )
    return str(basis) if basis is not None else None


def _complete_scope_contribution_available(
    data: dict[str, Any],
    metric_semantics: dict[str, Any],
) -> bool:
    basis = _contribution_basis(data, metric_semantics)
    if basis in COMPLETE_SCOPE_BASIS_VALUES:
        return True
    if basis in SOURCE_LIMITED_BASIS_VALUES:
        return False
    return data.get("rowset_complete") is True


def _contribution_withheld(
    row: dict[str, Any],
    data: dict[str, Any],
    metric_semantics: dict[str, Any],
) -> bool:
    if _complete_scope_contribution_available(data, metric_semantics):
        return False
    contribution_keys = (
        "cache_miss_contribution_pct",
        "current_cache_miss_contribution_pct",
        "origin_pressure_contribution_pct",
        "current_origin_pressure_contribution_pct",
        "current_total_cache_misses_for_contribution",
        "current_total_origin_pressure_score",
        "current_total_origin_pressure_for_contribution",
    )
    return any(_row_number(row, key) is not None for key in contribution_keys)


def _selected_bot_classes(scope: dict[str, Any], entity: dict[str, Any]) -> list[str]:
    selected = scope.get("selected_bot_classes")
    if isinstance(selected, list):
        return [str(value) for value in selected if not _is_blank(value)]
    if not _is_blank(entity.get("bot_class")):
        return [str(entity["bot_class"])]
    return []


def _derive_share_and_contribution_metrics(
    row: dict[str, Any],
    current: dict[str, float],
    deltas: dict[str, float],
    *,
    data: dict[str, Any],
    metric_semantics: dict[str, Any],
    scope: dict[str, Any],
    entity: dict[str, Any],
    contribution_totals: dict[str, float],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    share_denominators: dict[str, Any] = {}
    not_evaluated: list[dict[str, Any]] = []
    confidence_reasons: list[str] = []
    complete_contribution = _complete_scope_contribution_available(
        data,
        metric_semantics,
    )

    selected_bot_classes = _selected_bot_classes(scope, entity)
    if selected_bot_classes:
        share_denominators["selected_bot_classes"] = selected_bot_classes

    total_share_misses = _row_number(row, "current_total_cache_misses_for_share")
    selected_share_misses = _row_number(
        row,
        "current_selected_bot_class_cache_misses_for_share",
    )
    if total_share_misses is not None:
        share_denominators["current_total_cache_misses_for_share"] = clean_number(
            total_share_misses
        )
    if selected_share_misses is not None:
        share_denominators[
            "current_selected_bot_class_cache_misses_for_share"
        ] = clean_number(selected_share_misses)

    bot_miss_share = _row_number(row, "bot_miss_share_pct", "current_bot_miss_share_pct")
    computed_bot_miss_share = _pct_from_parts(
        selected_share_misses,
        total_share_misses,
    )
    if bot_miss_share is None:
        bot_miss_share = computed_bot_miss_share
    if bot_miss_share is not None:
        current["bot_miss_share_pct"] = bot_miss_share
        if total_share_misses is not None or selected_share_misses is not None:
            share_denominators[
                "bot_miss_share_basis"
            ] = "selected_bot_classes_over_path_all_bot_classes_and_asn_types"

    total_path_pressure = _row_number(row, "current_total_origin_pressure_for_path")
    selected_path_pressure = _row_number(
        row,
        "current_selected_bot_class_origin_pressure_for_path",
    )
    if total_path_pressure is not None:
        share_denominators["current_total_origin_pressure_for_path"] = clean_number(
            total_path_pressure
        )
    if selected_path_pressure is not None:
        share_denominators[
            "current_selected_bot_class_origin_pressure_for_path"
        ] = clean_number(selected_path_pressure)

    bot_pressure_share = _row_number(
        row,
        "bot_origin_pressure_share_pct",
        "current_bot_origin_pressure_share_pct",
    )
    computed_bot_pressure_share = _pct_from_parts(
        selected_path_pressure,
        total_path_pressure,
    )
    if bot_pressure_share is None:
        bot_pressure_share = computed_bot_pressure_share
    if bot_pressure_share is not None:
        current["bot_origin_pressure_share_pct"] = bot_pressure_share
        if total_path_pressure is not None or selected_path_pressure is not None:
            share_denominators[
                "bot_origin_pressure_share_basis"
            ] = "selected_bot_classes_over_path_all_bot_classes_and_asn_types"

    contribution_basis = _contribution_basis(data, metric_semantics)
    if contribution_basis is None and data.get("rowset_complete") is True:
        contribution_basis = "rowset_complete"
    if contribution_basis is not None:
        share_denominators["cache_miss_contribution_basis"] = contribution_basis
        share_denominators["origin_pressure_contribution_basis"] = contribution_basis

    if complete_contribution:
        total_contribution_misses = _row_number(
            row,
            "current_total_cache_misses_for_contribution",
        )
        if total_contribution_misses is None and data.get("rowset_complete") is True:
            total_contribution_misses = contribution_totals.get("cache_misses")
        if total_contribution_misses is not None:
            share_denominators[
                "current_total_cache_misses_for_contribution"
            ] = clean_number(total_contribution_misses)
        cache_miss_contribution = _row_number(
            row,
            "cache_miss_contribution_pct",
            "current_cache_miss_contribution_pct",
        )
        if cache_miss_contribution is None:
            cache_miss_contribution = _pct_from_parts(
                current.get("cache_misses"),
                total_contribution_misses,
            )
        if cache_miss_contribution is not None:
            deltas["cache_miss_contribution_pct"] = cache_miss_contribution

        total_origin_pressure = _row_number(
            row,
            "current_total_origin_pressure_score",
            "current_total_origin_pressure_for_contribution",
        )
        if total_origin_pressure is None and data.get("rowset_complete") is True:
            total_origin_pressure = contribution_totals.get("origin_pressure_score")
        if total_origin_pressure is not None:
            share_denominators[
                "current_total_origin_pressure_score"
            ] = clean_number(total_origin_pressure)
        origin_pressure_contribution = _row_number(
            row,
            "origin_pressure_contribution_pct",
            "current_origin_pressure_contribution_pct",
        )
        if origin_pressure_contribution is None:
            origin_pressure_contribution = _pct_from_parts(
                current.get("origin_pressure_score"),
                total_origin_pressure,
            )
        if origin_pressure_contribution is not None:
            deltas["origin_pressure_contribution_pct"] = origin_pressure_contribution
        confidence_reasons.append("complete_scope_contribution")
    elif _contribution_withheld(row, data, metric_semantics):
        for name in (
            "cache_miss_contribution_pct",
            "origin_pressure_contribution_pct",
        ):
            not_evaluated.append(
                {
                    "entity": entity,
                    "name": name,
                    "reason": "contribution_withheld_source_limited",
                }
            )
        confidence_reasons.append("contribution_withheld_source_limited")
    elif contribution_basis in SOURCE_LIMITED_BASIS_VALUES:
        confidence_reasons.append("contribution_withheld_source_limited")

    return share_denominators, not_evaluated, confidence_reasons


def _current_bucket_is_partial(data: dict[str, Any]) -> bool:
    current_window = data.get("current_window")
    return (
        data.get("partial_current_bucket") is True
        or data.get("current_bucket_partial") is True
        or (
            isinstance(current_window, dict)
            and (
                current_window.get("partial") is True
                or current_window.get("partial_bucket") is True
            )
        )
    )


def _table_confidence_reasons(
    data: dict[str, Any],
    dimensions: list[str],
    normalization: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    table_used = str(data.get("table_used") or "")

    if "trusted_context" in data:
        reasons.append("caller_supplied_json_confidence_cap")
    if data.get("summary_table_used") is True:
        reasons.append("summary_table_used")
    if (
        data.get("summary_table_used") is False
        or data.get("raw_table_fallback") is True
        or table_used in {"bot_detection", "bot_detection_siem"}
    ):
        reasons.append("raw_table_fallback")

    if (
        table_used.startswith("bot_agg_path_")
        or data.get("path_summary_used") is True
    ):
        reasons.append("path_summary_used")

    missing_retained = data.get("missing_retained_dimensions")
    if missing_retained:
        reasons.append("missing_retained_dimension")
    else:
        row_dimensions = [
            dimension
            for dimension in dimensions
            if dimension != "request_host"
        ]
        if frozenset(row_dimensions) in SUPPORTED_DIMENSION_SET_KEYS:
            reasons.append("retained_dimensions_fit")

    if normalization.get("method") == "duration_normalized_additive_metrics":
        reasons.append("baseline_duration_normalized")
    if _current_bucket_is_partial(data):
        reasons.append("partial_current_bucket")
    return reasons


def _candidate_count_confidence_reasons(
    current: dict[str, float],
    baseline: dict[str, float],
) -> list[str]:
    reasons: list[str] = []
    current_requests = current.get("requests")
    baseline_requests = baseline.get("requests")
    current_cache_misses = current.get("cache_misses")

    if current_requests is not None and current_requests >= SUFFICIENT_REQUEST_COUNT:
        reasons.append("current_count_sufficient")
    if baseline_requests is not None and baseline_requests >= SUFFICIENT_REQUEST_COUNT:
        reasons.append("baseline_count_sufficient")

    sparse = False
    if current_requests is not None and current_requests < SUFFICIENT_REQUEST_COUNT:
        sparse = True
    if (
        current_cache_misses is not None
        and current_cache_misses < SUFFICIENT_CACHE_MISS_COUNT
    ):
        sparse = True
    if (
        baseline
        and baseline_requests is not None
        and baseline_requests < SUFFICIENT_REQUEST_COUNT
    ):
        sparse = True
    if sparse:
        reasons.append("sparse_counts")
    return reasons


def _response_bytes_optional_metadata(
    current: dict[str, float],
    baseline: dict[str, float],
) -> dict[str, Any]:
    current_bytes = current.get("response_bytes")
    baseline_bytes = baseline.get("response_bytes")
    if current_bytes is None and baseline_bytes is None:
        return {
            "available": False,
            "reason": "not_present_in_selected_path_summary",
        }

    metadata: dict[str, Any] = {"available": True}
    if current_bytes is not None:
        metadata["current"] = clean_number(current_bytes)
    if baseline_bytes is not None:
        metadata["baseline"] = clean_number(baseline_bytes)
    return metadata


def _bot_summary_context_metadata(data: dict[str, Any]) -> dict[str, Any] | None:
    context = data.get("bot_summary_context")
    if context is None:
        return None
    if not isinstance(context, dict):
        return {
            "available": False,
            "reason": "malformed_bot_summary_context",
            "limitations": ["host_scope_context_not_path_level_evidence"],
        }

    metadata = dict(context)
    metadata["available"] = True
    limitations = list(metadata.get("limitations", []))
    if "host_scope_context_not_path_level_evidence" not in limitations:
        limitations.append("host_scope_context_not_path_level_evidence")
    metadata["limitations"] = limitations
    return metadata


def _candidate_limitations(
    confidence_reasons: list[str],
    not_evaluated: list[dict[str, Any]],
    optional_metadata: dict[str, Any],
    data: dict[str, Any],
) -> list[str]:
    limitations: set[str] = set()
    reason_set = set(confidence_reasons)
    if "query_string_cardinality_approximate" in reason_set:
        limitations.add("query_string_cardinality_approximate")
    if "raw_table_fallback" in reason_set:
        limitations.add("raw_table_fallback")
    if "missing_retained_dimension" in reason_set:
        limitations.add("missing_retained_dimension")
    if "contribution_withheld_source_limited" in reason_set:
        limitations.add("contribution_withheld_source_limited")
    if "partial_current_bucket" in reason_set:
        limitations.add("partial_current_bucket")

    response_metadata = optional_metadata.get("response_bytes")
    if isinstance(response_metadata, dict) and not response_metadata.get("available"):
        limitations.add("response_byte_metadata_not_available")
    bot_context = optional_metadata.get("bot_summary_context")
    if isinstance(bot_context, dict):
        limitations.update(
            limitation
            for limitation in bot_context.get("limitations", [])
            if isinstance(limitation, str)
        )

    if any(entry.get("reason") == "baseline_absent" for entry in not_evaluated):
        limitations.add("missing_baseline")
    if any(
        entry.get("name")
        in {"cache_miss_contribution_pct", "origin_pressure_contribution_pct"}
        and entry.get("reason") == "complete_scope_denominator_absent"
        for entry in not_evaluated
    ):
        limitations.add("contribution_denominator_absent")
    if any(
        entry.get("name")
        in {"cache_miss_contribution_pct", "origin_pressure_contribution_pct"}
        and entry.get("reason") == "contribution_withheld_source_limited"
        for entry in not_evaluated
    ):
        limitations.add("contribution_withheld_source_limited")
    if data.get("source_limited") is True or data.get("rowset_complete") is False:
        limitations.add("source_limited_rowset")
    raw_fallback_scope = str(data.get("raw_fallback_scope") or "").lower()
    if raw_fallback_scope == "broad" or data.get("broad_raw_fallback") is True:
        limitations.add("broad_raw_table_fallback")
    return sorted(limitations)


def _confidence_label(
    confidence_reasons: list[str],
    limitations: list[str],
    *,
    trusted_context_complete: bool,
) -> str:
    if (
        trusted_context_complete
        and "direct_mcp_trusted_context" in confidence_reasons
        and not (set(confidence_reasons) & LOW_CONFIDENCE_REASONS)
        and not (set(confidence_reasons) & MEDIUM_ONLY_CONFIDENCE_REASONS)
        and not (set(limitations) & LOW_CONFIDENCE_LIMITATIONS)
    ):
        return "high"
    if set(confidence_reasons) & LOW_CONFIDENCE_REASONS:
        return "low"
    if set(limitations) & LOW_CONFIDENCE_LIMITATIONS:
        return "low"
    return "medium"


def _lowest_confidence(labels: list[str]) -> str:
    if not labels:
        return "medium"
    return min(labels, key=lambda label: CONFIDENCE_ORDER.get(label, 1))


def _truthy_context_value(context: dict[str, Any], *keys: str) -> bool:
    return any(bool(context.get(key)) for key in keys)


def _trusted_context_complete(
    trusted_context: dict[str, Any] | None,
    dimensions: list[str],
) -> bool:
    if not isinstance(trusted_context, dict):
        return False

    retained_dimensions = trusted_context.get("retained_dimensions")
    retained_dimensions_fit = trusted_context.get("retained_dimensions_fit") is True
    if isinstance(retained_dimensions, list):
        retained = {str(dimension) for dimension in retained_dimensions}
        expected = {
            dimension
            for dimension in dimensions
            if dimension != "request_host"
        }
        retained_dimensions_fit = retained_dimensions_fit or expected.issubset(retained)

    digest_proven = bool(trusted_context.get("query_result_digest")) or (
        bool(trusted_context.get("query_digest"))
        and bool(trusted_context.get("result_digest"))
    )
    direct_mcp = (
        trusted_context.get("direct_mcp_trusted_context") is True
        or trusted_context.get("source") == "direct_mcp"
    )

    return all(
        (
            direct_mcp,
            _truthy_context_value(trusted_context, "table_metadata", "table_info"),
            retained_dimensions_fit,
            digest_proven,
            _truthy_context_value(trusted_context, "comparable_windows"),
            _truthy_context_value(trusted_context, "current_count_sufficient"),
            _truthy_context_value(trusted_context, "baseline_count_sufficient"),
            _truthy_context_value(trusted_context, "complete_scope_contribution"),
        )
    )


def _detector_not_evaluated_entries(
    entity: dict[str, Any],
    current: dict[str, float],
    baseline: dict[str, float],
    deltas: dict[str, float],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if "unique_query_strings" not in current:
        entries.append(
            {
                "entity": entity,
                "name": "query_string_diversity_detector",
                "reason": "query_string_cardinality_absent",
                "missing_inputs": ["current.unique_query_strings"],
            }
        )
    if "cache_misses" not in current:
        entries.append(
            {
                "entity": entity,
                "name": "cache_miss_movement_detector",
                "reason": "cache_miss_metric_absent",
                "missing_inputs": ["current.cache_misses"],
            }
        )
    if not baseline:
        entries.append(
            {
                "entity": entity,
                "name": "baseline_comparison",
                "reason": "baseline_absent",
            }
        )
    if current.get("origin_p95_ms") is None:
        entries.append(
            {
                "entity": entity,
                "name": "origin_pressure_detector",
                "reason": "origin_p95_absent",
                "missing_inputs": ["current.origin_p95_ms"],
            }
        )
    elif current.get("origin_p95_ms") == 0:
        entries.append(
            {
                "entity": entity,
                "name": "origin_pressure_detector",
                "reason": "origin_p95_zero",
            }
        )
    if (
        current.get("bot_miss_share_pct") is None
        and current.get("bot_origin_pressure_share_pct") is None
    ):
        entries.append(
            {
                "entity": entity,
                "name": "bot_attribution_detector",
                "reason": "bot_class_share_unavailable",
                "missing_inputs": [
                    "current.bot_miss_share_pct",
                    "current.bot_origin_pressure_share_pct",
                ],
            }
        )
    if "origin_pressure_contribution_pct" not in deltas:
        entries.append(
            {
                "entity": entity,
                "name": "origin_pressure_contribution_pct",
                "reason": "complete_scope_denominator_absent",
                "missing_inputs": ["current_total_origin_pressure_score"],
            }
        )
    return entries


def _query_string_guard(current: dict[str, float]) -> bool:
    return (
        current.get("requests", 0) >= 1000
        and current.get("unique_query_strings", 0) >= 100
        and current.get("qs_diversity_ratio", 0) >= 0.5
    )


def _cache_miss_guard(current: dict[str, float]) -> bool:
    return current.get("requests", 0) >= 1000 and current.get("cache_misses", 0) >= 100


def _origin_pressure_guard(current: dict[str, float]) -> bool:
    return current.get("cache_misses", 0) >= 100 and current.get("origin_p95_ms", 0) > 0


def _bot_attribution_guard(current: dict[str, float]) -> bool:
    return current.get("cache_misses", 0) >= 100 and (
        current.get("bot_miss_share_pct", 0) >= 25
        or current.get("bot_origin_pressure_share_pct", 0) >= 25
    )


def _finding_types(current: dict[str, float]) -> list[str]:
    finding_types: list[str] = []
    if _query_string_guard(current):
        finding_types.append("cache_busting_candidate")
    if _cache_miss_guard(current):
        finding_types.append("cache_miss_movement_candidate")
    if _origin_pressure_guard(current):
        finding_types.append("origin_impact_candidate")
    if _bot_attribution_guard(current):
        if current.get("bot_miss_share_pct", 0) >= 25:
            finding_types.append("bot_attributable_cache_misses")
        if current.get("bot_origin_pressure_share_pct", 0) >= 25:
            finding_types.append("bot_attributable_origin_pressure")
    return finding_types


def _feature(
    name: str,
    points: int,
    value: float,
    threshold: float | dict[str, float],
) -> dict[str, Any]:
    return {
        "name": name,
        "points": points,
        "value": clean_number(value),
        "threshold": threshold,
    }


def _score_features(
    current: dict[str, float],
    deltas: dict[str, float],
) -> tuple[list[dict[str, Any]], int, str]:
    features: list[dict[str, Any]] = []

    qs_ratio = current.get("qs_diversity_ratio")
    if qs_ratio is not None and qs_ratio >= SCORING_THRESHOLDS["high_query_string_diversity"]:
        features.append(
            _feature(
                "high_query_string_diversity",
                20,
                qs_ratio,
                SCORING_THRESHOLDS["high_query_string_diversity"],
            )
        )
    elif (
        qs_ratio is not None
        and SCORING_THRESHOLDS["moderate_query_string_diversity"]
        <= qs_ratio
        < SCORING_THRESHOLDS["high_query_string_diversity"]
    ):
        features.append(
            _feature(
                "moderate_query_string_diversity",
                10,
                qs_ratio,
                SCORING_THRESHOLDS["moderate_query_string_diversity"],
            )
        )

    qs_delta = deltas.get("qs_diversity_delta")
    if qs_delta is not None and qs_delta >= SCORING_THRESHOLDS["query_string_diversity_increased"]:
        features.append(
            _feature(
                "query_string_diversity_increased",
                10,
                qs_delta,
                SCORING_THRESHOLDS["query_string_diversity_increased"],
            )
        )

    miss_rate = current.get("miss_rate_pct")
    if miss_rate is not None and miss_rate >= SCORING_THRESHOLDS["high_miss_rate"]:
        features.append(
            _feature(
                "high_miss_rate",
                15,
                miss_rate,
                SCORING_THRESHOLDS["high_miss_rate"],
            )
        )

    miss_rate_delta = deltas.get("miss_rate_delta_pp")
    if miss_rate_delta is not None and miss_rate_delta >= SCORING_THRESHOLDS["miss_rate_increased"]:
        features.append(
            _feature(
                "miss_rate_increased",
                15,
                miss_rate_delta,
                SCORING_THRESHOLDS["miss_rate_increased"],
            )
        )

    origin_p95_delta = deltas.get("origin_p95_delta_ms")
    origin_p95_pct_change = deltas.get("origin_p95_pct_change")
    if (
        origin_p95_delta is not None
        and origin_p95_pct_change is not None
        and origin_p95_delta >= SCORING_THRESHOLDS["origin_tail_latency_delta_ms"]
        and origin_p95_pct_change >= SCORING_THRESHOLDS["origin_tail_latency_pct_change"]
    ):
        features.append(
            _feature(
                "origin_tail_latency_increased",
                15,
                origin_p95_delta,
                {
                    "origin_p95_delta_ms": SCORING_THRESHOLDS[
                        "origin_tail_latency_delta_ms"
                    ],
                    "origin_p95_pct_change": SCORING_THRESHOLDS[
                        "origin_tail_latency_pct_change"
                    ],
                },
            )
        )

    origin_contribution = deltas.get("origin_pressure_contribution_pct")
    if (
        origin_contribution is not None
        and origin_contribution >= SCORING_THRESHOLDS["origin_pressure_contributor"]
    ):
        features.append(
            _feature(
                "origin_pressure_contributor",
                15,
                origin_contribution,
                SCORING_THRESHOLDS["origin_pressure_contributor"],
            )
        )

    bot_share = max(
        current.get("bot_miss_share_pct", 0),
        current.get("bot_origin_pressure_share_pct", 0),
    )
    if bot_share >= SCORING_THRESHOLDS["bot_attributable_majority"]:
        features.append(
            _feature(
                "bot_attributable_majority",
                10,
                bot_share,
                SCORING_THRESHOLDS["bot_attributable_majority"],
            )
        )

    current_requests = current.get("requests")
    if (
        current_requests is not None
        and current_requests >= SCORING_THRESHOLDS["large_current_volume"]
    ):
        features.append(
            _feature(
                "large_current_volume",
                5,
                current_requests,
                SCORING_THRESHOLDS["large_current_volume"],
            )
        )

    score = min(sum(feature["points"] for feature in features), 100)
    if score >= 70:
        band = "high"
    elif score >= 45:
        band = "medium"
    elif score >= 20:
        band = "low"
    else:
        band = "informational"
    return features, score, band


def _volume_sufficient(candidate: dict[str, Any]) -> bool:
    current = candidate.get("current", {})
    return current.get("requests", 0) >= 1000 or current.get("cache_misses", 0) >= 100


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    current = candidate.get("current", {})
    deltas = candidate.get("deltas", {})
    return (
        0 if _volume_sufficient(candidate) else 1,
        -candidate.get("candidate_score", 0),
        -deltas.get("origin_pressure_delta", 0),
        -deltas.get("cache_misses", 0),
        -current.get("cache_misses", 0),
        -current.get("requests", 0),
    )


def _derive_candidates(
    rows: list[dict[str, Any]],
    dimensions: list[str],
    scope: dict[str, Any],
    metric_semantics: dict[str, Any],
    normalization: dict[str, Any],
    data: dict[str, Any],
    *,
    trusted_context_complete: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    candidates: list[dict[str, Any]] = []
    not_evaluated: list[dict[str, Any]] = []
    confidence_reasons: list[str] = []
    base_confidence_reasons = _table_confidence_reasons(
        data,
        dimensions,
        normalization,
    )
    if trusted_context_complete:
        base_confidence_reasons.append("direct_mcp_trusted_context")
    bot_summary_context_metadata = _bot_summary_context_metadata(data)

    metric_rows = _metric_rows(rows, dimensions, scope)

    derived_rows: list[dict[str, Any]] = []
    for metric_row in metric_rows:
        current, current_reasons = _derive_period_metrics(
            metric_row["current"],
            metric_semantics,
        )
        normalized_baseline = _normalize_baseline_metrics(
            metric_row["baseline"],
            normalization,
        )
        baseline, baseline_reasons = _derive_period_metrics(
            normalized_baseline,
            metric_semantics,
        )
        deltas = _delta_metrics(current, baseline)
        derived_rows.append(
            {
                "metric_row": metric_row,
                "current": current,
                "current_reasons": current_reasons,
                "baseline": baseline,
                "baseline_reasons": baseline_reasons,
                "deltas": deltas,
            }
        )

    contribution_totals: dict[str, float] = {}
    if data.get("rowset_complete") is True:
        cache_miss_values = [
            row["current"].get("cache_misses")
            for row in derived_rows
            if row["current"].get("cache_misses") is not None
        ]
        origin_pressure_values = [
            row["current"].get("origin_pressure_score")
            for row in derived_rows
            if row["current"].get("origin_pressure_score") is not None
        ]
        if cache_miss_values:
            contribution_totals["cache_misses"] = sum(cache_miss_values)
        if origin_pressure_values:
            contribution_totals["origin_pressure_score"] = sum(origin_pressure_values)

    for derived_row in derived_rows:
        metric_row = derived_row["metric_row"]
        source_row = metric_row.get("source_row", {})
        entity = metric_row["entity"]
        current = derived_row["current"]
        current_reasons = derived_row["current_reasons"]
        baseline = derived_row["baseline"]
        baseline_reasons = derived_row["baseline_reasons"]
        deltas = derived_row["deltas"]
        share_denominators, share_not_evaluated, share_confidence_reasons = (
            _derive_share_and_contribution_metrics(
                source_row,
                current,
                deltas,
                data=data,
                metric_semantics=metric_semantics,
                scope=scope,
                entity=entity,
                contribution_totals=contribution_totals,
            )
        )
        candidate_not_evaluated = _not_evaluated_entries(
            entity,
            current,
            baseline,
            deltas,
        )
        candidate_not_evaluated.extend(share_not_evaluated)
        candidate_not_evaluated.extend(
            _detector_not_evaluated_entries(entity, current, baseline, deltas)
        )
        features, score, band = _score_features(current, deltas)
        optional_metadata: dict[str, Any] = {
            "response_bytes": _response_bytes_optional_metadata(current, baseline),
        }
        if bot_summary_context_metadata is not None:
            optional_metadata["bot_summary_context"] = bot_summary_context_metadata
        candidate_reasons = sorted(
            set(
                base_confidence_reasons
                + current_reasons
                + baseline_reasons
                + share_confidence_reasons
                + _candidate_count_confidence_reasons(current, baseline)
            )
        )
        limitations = _candidate_limitations(
            candidate_reasons,
            candidate_not_evaluated,
            optional_metadata,
            data,
        )
        confidence = _confidence_label(
            candidate_reasons,
            limitations,
            trusted_context_complete=trusted_context_complete,
        )

        candidate: dict[str, Any] = {
            "entity": entity,
            "current": _clean_metric_map(current),
            "baseline": _clean_metric_map(baseline),
            "deltas": _clean_metric_map(deltas),
            "candidate_score": score,
            "candidate_band": band,
            "features": features,
            "finding_types": _finding_types(current),
            "not_evaluated": candidate_not_evaluated,
            "confidence": confidence,
            "confidence_reasons": candidate_reasons,
            "limitations": limitations,
            "optional_metadata": optional_metadata,
        }
        if share_denominators:
            candidate["share_denominators"] = share_denominators
        confidence_reasons.extend(candidate_reasons)
        candidates.append(candidate)
        not_evaluated.extend(candidate_not_evaluated)

    candidates.sort(key=_candidate_sort_key)
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank

    return candidates, not_evaluated, sorted(set(confidence_reasons))


def build_report(
    value: Any,
    trusted_context: dict[str, Any] | None = None,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    data = _require_mapping(value)
    _validate_metric_or_analysis_type(data)
    dimensions = _validate_dimensions(data)
    rows = _validated_rows(data)
    current_window = _validate_current_window(data)
    scope = _validate_rows(data, dimensions, rows)
    metric_semantics = _validate_metric_semantics(data, rows)

    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative.")
    baseline_normalization = _baseline_normalization(
        data,
        current_window,
        rows,
    )
    trusted_context_is_complete = _trusted_context_complete(trusted_context, dimensions)
    candidates, not_evaluated, _derivation_confidence_reasons = _derive_candidates(
        rows,
        dimensions,
        scope,
        metric_semantics,
        baseline_normalization,
        data,
        trusted_context_complete=trusted_context_is_complete,
    )
    if limit is not None:
        candidates = candidates[:limit]
        for rank, candidate in enumerate(candidates, start=1):
            candidate["rank"] = rank
        emitted_entities = {tuple(sorted(candidate["entity"].items())) for candidate in candidates}
        not_evaluated = [
            entry
            for entry in not_evaluated
            if tuple(sorted(entry.get("entity", {}).items())) in emitted_entities
        ]

    confidence_reasons = sorted(
        {
            reason
            for candidate in candidates
            for reason in candidate.get("confidence_reasons", [])
        }
    )
    limitations = sorted(
        {
            limitation
            for candidate in candidates
            for limitation in candidate.get("limitations", [])
        }
    )
    optional_metadata: dict[str, Any] = {}
    bot_summary_context_metadata = _bot_summary_context_metadata(data)
    if bot_summary_context_metadata is not None:
        optional_metadata["bot_summary_context"] = bot_summary_context_metadata

    report_metric_semantics = {
        "origin_pressure_score": "proxy_misses_times_origin_p95_seconds",
        **metric_semantics,
    }
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "analysis_type": ANALYSIS_TYPE,
        "source_skill": data.get("source_skill", "bot-insights"),
        "comparison_type": data.get(
            "comparison_type",
            "current_only" if not data.get("baseline_windows") else "unspecified",
        ),
        "granularity": data.get("granularity"),
        "table_used": data.get("table_used"),
        "summary_table_used": data.get("summary_table_used"),
        "scope": scope,
        "dimensions": dimensions,
        "current_window": current_window,
        "baseline_windows": data.get("baseline_windows", []),
        "baseline_normalization": baseline_normalization,
        "metric_semantics": report_metric_semantics,
        "candidates": candidates,
        "not_evaluated": not_evaluated,
        "interpretation_constraints": INTERPRETATION_CONSTRAINTS,
        "confidence": _lowest_confidence(
            [candidate.get("confidence", "medium") for candidate in candidates]
        ),
        "confidence_reasons": sorted(set(confidence_reasons)),
        "limitations": limitations,
    }
    if optional_metadata:
        report["optional_metadata"] = optional_metadata
    return report


def main() -> int:
    args = parse_args()
    try:
        report = build_report(json.loads(read_input(args)), limit=args.limit)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
