#!/usr/bin/env python3
"""Normalize Bot Insights attribution aggregates into a conservative report."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence


ATTRIBUTION_SCHEMA = "bot_attribution_report.v1"
ERROR_SCHEMA = "bot_attribution_error.v1"
SQL_GENERATOR_NAME = "bot-insights-attribution-sql"
SQL_GENERATOR_VERSION = "1.0.0"
SQL_TEMPLATE_SCHEMA = "bot_attribution_sql_template.v1"
SQL_TEMPLATE_ID = "full_scope_joined_pre_limit_v1"
TRUSTED_WRAPPER_NAME = "bot-insights-attribution-runner"
TRUSTED_WRAPPER_VERSION = "1.0.0"
TRUSTED_RESULT_ORIGIN = "direct_mcp_tool_output"
TRUSTED_METADATA_ORIGIN = "direct_hydrolix_table_metadata"
TRUSTED_EVIDENCE_SOURCE = "trusted_template_generator"
DIGEST_SCHEMA_VERSION = "digest_payload_v1"
TRUSTED_WRAPPER_AVAILABLE = False
PROVIDED_CONTRIBUTION_TOLERANCE_PP = Decimal("0.01")
SAMPLE_ENTITY_VALUES_LIMIT = 10
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ANALYSIS_TYPES = {
    "aggregate_delta_attribution",
    "policy_displacement",
}

INTERPRETATION_CONSTRAINTS = [
    "attribution_from_aggregate_deltas",
    "movement_only",
    "no_causal_claim",
    "llm_may_summarize_structured_evidence_only",
]

WRAPPER_KEYS = (
    "input_doc",
    "input",
    "payload",
    "mcp_result",
    "mcpResult",
    "result",
    "aggregate",
    "value",
)

REPORT_FIELDS = {
    "baseline_method",
    "baseline_value_semantic",
    "baseline_windows",
    "comparison_type",
    "contribution_basis",
    "current_window",
    "dimensions",
    "filters",
    "granularity",
    "grouped_dimensions",
    "metric",
    "metric_kind",
    "output_limit",
    "output_limit_applied",
    "row_shape",
    "rowset_complete",
    "scope",
    "source_limit_applied",
    "summary_table_used",
    "table_used",
    "applied_scope_filters",
    "analysis_type",
    "policy_change",
    "policy_change_window",
    "reviewed_policy",
    "target_effect",
}

TRUST_METADATA_FIELDS = {
    "baseline_normalization",
    "generator_name",
    "generator_version",
    "limit_stage",
    "metadata_fingerprint",
    "metadata_fixture_identity",
    "metadata_origin",
    "metadata_retrieval_identity",
    "merge_expressions",
    "query_fingerprint",
    "result_digest",
    "selected_columns",
    "selected_table",
    "source_limit_stage",
    "template_id",
    "trusted_context",
    "trusted_evidence",
}

METADATA_KEYS = {
    "absolute_delta",
    "abs_delta",
    "analysis_type",
    "baseline",
    "baseline_method",
    "baseline_normalization",
    "baseline_support_count",
    "baseline_support_normalized",
    "baseline_support_raw",
    "baseline_value_semantic",
    "baseline_windows",
    "bucket",
    "caller_metric_kind_assertion",
    "columns",
    "comparison_type",
    "complete_scope_total_abs_delta",
    "contribution_basis",
    "contribution_pct",
    "current",
    "current_support_count",
    "current_support_raw",
    "current_window",
    "data",
    "dimension",
    "dimensions",
    "entity",
    "evidence_source",
    "filters",
    "generator_name",
    "granularity",
    "grouped_dimensions",
    "input_assertions",
    "label",
    "limit",
    "metadata",
    "metric",
    "metric_kind",
    "output_limit",
    "output_limit_applied",
    "pct_change",
    "period",
    "policy_change",
    "policy_change_window",
    "query_fingerprint",
    "result_digest",
    "reviewed_policy",
    "row_shape",
    "rowset_complete",
    "rows",
    "schema_version",
    "scope",
    "scorecard_export_safe",
    "source_limit_applied",
    "summary_table_used",
    "support_count",
    "support_raw",
    "table_used",
    "target_effect",
    "template_id",
    "time",
    "timestamp",
    "value",
    "window",
    "window_end",
    "window_start",
} | TRUST_METADATA_FIELDS

DIMENSION_INFERENCE_EXACT_EXCLUSIONS = {
    "absolute_delta",
    "abs_delta",
    "baseline",
    "baseline_support_count",
    "baseline_support_normalized",
    "baseline_support_raw",
    "caller_metric_kind_assertion",
    "columns",
    "complete_scope_total_abs_delta",
    "contribution_pct",
    "current",
    "current_support_count",
    "current_support_raw",
    "data",
    "entity",
    "evidence_source",
    "generator_name",
    "label",
    "metadata",
    "pct_change",
    "period",
    "query_fingerprint",
    "result_digest",
    "rows",
    "scorecard_export_safe",
    "support_count",
    "support_raw",
    "template_id",
    "time",
    "timestamp",
    "value",
    "window",
    "window_end",
    "window_start",
}

ROW_SHAPE_PERIOD_ALIASES = {
    "after": "current",
    "baseline": "baseline",
    "before": "baseline",
    "current": "current",
}

BASELINE_METHODS = {
    "single_previous_window",
    "mean_of_baseline_windows",
    "duration_weighted_mean_of_baseline_windows",
    "externally_precomputed_baseline",
}

BASELINE_VALUE_SEMANTICS = {
    "raw_total_window",
    "duration_normalized_to_current_window",
    "externally_precomputed_baseline",
}
SQL_LIMIT_STAGES = {
    "none",
    "after_denominator",
    "before_denominator",
}

METRIC_ALLOWLIST = {
    "requests": {
        "metric_kind": "additive_count",
        "aliases": (
            "requests",
            "request_count",
            "total_requests",
            "cnt_all",
            "current_requests",
            "baseline_requests",
        ),
    },
    "blocked_requests": {
        "metric_kind": "additive_count",
        "aliases": (
            "blocked_requests",
            "siem_blocked_requests",
            "cnt_blocked",
        ),
    },
    "bot_share_pct": {
        "metric_kind": "ratio",
        "aliases": (
            "bot_share_pct",
            "bot_share_percentage",
        ),
    },
}

CURRENT_SUPPORT_KEYS = (
    "current_support_raw",
    "current_support_count",
    "current_count",
    "support_current",
    "support_raw_current",
    "current.support_raw",
)
BASELINE_SUPPORT_KEYS = (
    "baseline_support_raw",
    "baseline_support_count",
    "baseline_count",
    "support_baseline",
    "support_raw_baseline",
    "baseline.support_raw",
)
PERIOD_SUPPORT_KEYS = (
    "support_raw",
    "support_count",
    "count",
    "requests",
    "cnt_all",
)
BASELINE_SUPPORT_NORMALIZED_KEYS = (
    "baseline_support_normalized",
    "support_normalized_baseline",
    "baseline.support_normalized",
)

LIMITATION_MESSAGES: dict[str, tuple[str, str]] = {
    "aggregate_rows_only": (
        "info",
        "Attribution is based on pre-aggregated current and baseline rows, not raw request inspection.",
    ),
    "no_causal_claim": (
        "required",
        "Movers explain observed aggregate delta but do not prove cause.",
    ),
    "contribution_withheld": (
        "warning",
        "Contribution percentage was not computed from a limited or incomplete rowset.",
    ),
    "period_absence_not_trusted": (
        "warning",
        "One-sided rows were excluded because public JSON cannot prove trusted period absence or zero-fill.",
    ),
    "lifecycle_support_missing": (
        "warning",
        "Lifecycle labels were not evaluated for some rows because support fields were missing or unsupported.",
    ),
    "metadata_poor_input": (
        "warning",
        "Plain MCP-style rows lacked enough metadata for stronger confidence.",
    ),
    "dimensions_inferred": (
        "info",
        "Dimensions were inferred from row columns because none were explicitly provided.",
    ),
    "caller_assertion_not_trusted": (
        "warning",
        "Caller-supplied completeness, contribution, or scorecard metadata remained assertion-only.",
    ),
    "unsupported_summary_dimension_set": (
        "warning",
        "The selected summary table does not retain every requested grouped dimension.",
    ),
    "unsupported_summary_filter": (
        "warning",
        "The selected summary table does not retain every requested scope or filter column.",
    ),
    "trusted_context_missing": (
        "warning",
        "No in-process trusted context was supplied; public JSON remains assertion-only.",
    ),
    "trusted_context_invalid": (
        "warning",
        "The supplied trusted context did not match the reviewed v1 shape.",
    ),
    "trusted_context_digest_mismatch": (
        "warning",
        "The supplied trusted context result digest did not match the recomputed digest.",
    ),
    "trusted_evidence_missing": (
        "warning",
        "No typed trusted evidence list was supplied in trusted_context.",
    ),
    "trusted_evidence_mismatch": (
        "warning",
        "Trusted evidence did not match the normalized report contract.",
    ),
    "trusted_wrapper_unavailable": (
        "warning",
        "This package does not ship the reviewed direct-MCP wrapper needed to unlock trust.",
    ),
    "query_fingerprint_missing": (
        "warning",
        "Trusted context or evidence was missing query_fingerprint.",
    ),
    "result_digest_missing": (
        "warning",
        "Trusted context or evidence was missing result_digest.",
    ),
    "provided_contribution_inconsistent": (
        "warning",
        "Provided contribution evidence was missing required consistency guarantees.",
    ),
    "duplicate_aggregation_not_trusted": (
        "warning",
        "Duplicate aggregation evidence cannot be used without the reviewed direct-MCP wrapper.",
    ),
}


def table_family(
    prefix: str,
    granularities: Iterable[str],
    retained_dimensions: Iterable[str],
    *,
    parent: str,
) -> dict[str, dict[str, Any]]:
    return {
        f"{prefix}_{granularity}": {
            "table": f"{prefix}_{granularity}",
            "granularity": granularity,
            "parent": parent,
            "retained_dimensions": tuple(retained_dimensions),
        }
        for granularity in granularities
    }


SUMMARY_TABLE_CATALOG: dict[str, dict[str, Any]] = {}
SUMMARY_TABLE_CATALOG.update(
    table_family(
        "bot_summary",
        ("minute", "hour", "day"),
        (
            "request_host",
            "hdx_cdn",
            "bot_class",
            "ai_category",
            "is_bot_traffic",
            "client_asn",
            "asn_type",
            "resource_category",
            "request_method",
        ),
        parent="bot_detection",
    )
)
SUMMARY_TABLE_CATALOG["bot_agg_hour"] = {
    "table": "bot_agg_hour",
    "granularity": "hour",
    "parent": "bot_detection",
    "retained_dimensions": ("request_host",),
}
SUMMARY_TABLE_CATALOG.update(
    table_family(
        "bot_agg_path",
        ("minute", "hour", "day"),
        ("request_host", "request_path_norm", "bot_class", "asn_type"),
        parent="bot_detection",
    )
)
SUMMARY_TABLE_CATALOG["bot_agg_asn_hour"] = {
    "table": "bot_agg_asn_hour",
    "granularity": "hour",
    "parent": "bot_detection",
    "retained_dimensions": ("request_host", "client_asn", "asn_type"),
}
SUMMARY_TABLE_CATALOG["bot_agg_traffic_hour"] = {
    "table": "bot_agg_traffic_hour",
    "granularity": "hour",
    "parent": "bot_detection",
    "retained_dimensions": ("request_host", "is_bot_traffic", "ai_category"),
}
SUMMARY_TABLE_CATALOG["bot_agg_ua_hour"] = {
    "table": "bot_agg_ua_hour",
    "granularity": "hour",
    "parent": "bot_detection",
    "retained_dimensions": ("request_host", "bot_class"),
}
SUMMARY_TABLE_CATALOG.update(
    table_family(
        "bot_agg_resource",
        ("minute", "hour", "day"),
        ("request_host", "resource_category"),
        parent="bot_detection",
    )
)
SUMMARY_TABLE_CATALOG.update(
    table_family(
        "bot_siem_summary",
        ("minute", "hour", "day"),
        ("request_host", "action_taken", "client_asn", "policy_id"),
        parent="bot_detection_siem",
    )
)
SUMMARY_TABLE_CATALOG.update(
    table_family(
        "bot_siem_filter_summary",
        ("minute", "hour", "day"),
        (
            "request_host",
            "client_asn",
            "is_bot_traffic",
            "ai_category",
            "resource_category",
        ),
        parent="bot_detection_siem",
    )
)
SUMMARY_TABLE_CATALOG.update(
    table_family(
        "bot_siem_class",
        ("minute", "hour", "day"),
        ("request_host", "client_asn", "akamai_canonical_bot_class"),
        parent="bot_detection_siem",
    )
)

SUMMARY_FILTER_ALWAYS_RETAINED = {"timestamp"}

CONTRIBUTION_REQUIRED_METADATA = [
    "trusted evidence for rowset_complete: true and contribution_basis: complete_rowset",
    "or contribution_basis: complete_scope_pre_limit with trusted complete-scope evidence and an identical denominator",
    "or contribution_basis: provided_complete_scope with trusted evidence that matches metric, dimensions, scope, and windows",
]
ZERO_FILL_REQUIRED_METADATA = [
    "trusted zero_fill_evidence.period_value_trust.<side>: complete_grouped_scope",
    "or trusted zero_fill_evidence.period_value_trust.<side>: trusted_full_scope_join",
]

METRIC_ALIAS_TO_CANONICAL: dict[str, str] = {}
for canonical_metric, metric_info in METRIC_ALLOWLIST.items():
    METRIC_ALIAS_TO_CANONICAL[canonical_metric] = canonical_metric
    for alias in metric_info["aliases"]:
        METRIC_ALIAS_TO_CANONICAL[alias] = canonical_metric


class InvalidInputError(Exception):
    """Typed invalid-input error for CLI and library callers."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str = "$",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.document = invalid_input_doc(code, message, path=path, details=details)


def invalid_input_doc(
    code: str,
    message: str,
    *,
    path: str = "$",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error = {
        "code": code,
        "message": message,
        "path": path,
    }
    if details:
        error["details"] = details
    return {
        "schema_version": ERROR_SCHEMA,
        "error_type": "invalid_input",
        "fatal": True,
        "errors": [error],
        "limitations": [],
    }


def raise_invalid(
    code: str,
    message: str,
    *,
    path: str = "$",
    details: dict[str, Any] | None = None,
) -> None:
    raise InvalidInputError(code, message, path=path, details=details)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute a conservative Bot Insights attribution report from aggregate JSON."
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
        "--metric",
        help="Metric to normalize, such as requests or cnt_all.",
    )
    parser.add_argument(
        "--dimensions",
        help="Comma-separated dimensions to echo in the report and row keys.",
    )
    parser.add_argument(
        "--analysis",
        choices=tuple(sorted(ANALYSIS_TYPES)),
        help="Analysis mode. Use policy_displacement for policy-change displacement review.",
    )
    parser.add_argument(
        "--min-count",
        type=float,
        default=100.0,
        help="Minimum current and baseline support count for medium confidence.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional maximum number of ranked movers to return.",
    )
    parser.add_argument(
        "--output",
        choices=("report",),
        default="report",
        help="Output mode. The standalone CLI exposes only the report artifact.",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


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


def to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text == "true":
            return True
        if text == "false":
            return False
    return None


def clean_number(value: float | int | None) -> float | int | None:
    if value is None:
        return None
    rounded = round(float(value), 6)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def direction(delta: float) -> str:
    if delta > 0:
        return "increase"
    if delta < 0:
        return "decrease"
    return "no_change"


def pct_change(current: float, baseline: float) -> float:
    return (current - baseline) / max(baseline, 1.0) * 100.0


def normalize_options(options: Any) -> dict[str, Any]:
    if options is None:
        return {}
    if isinstance(options, argparse.Namespace):
        return vars(options).copy()
    if isinstance(options, dict):
        return dict(options)
    raise TypeError("options must be a dict, argparse.Namespace, or None")


def normalize_analysis_type(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text not in ANALYSIS_TYPES:
        raise_invalid(
            "analysis_type_invalid",
            f"Unsupported analysis_type '{value}'.",
            details={"analysis_type": value, "supported_analysis_types": sorted(ANALYSIS_TYPES)},
        )
    return text


def resolve_analysis_type(payload: Any, metadata: dict[str, Any], options: dict[str, Any]) -> str:
    cli_analysis = normalize_analysis_type(options.get("analysis"))
    input_analysis = normalize_analysis_type(resolve_value(payload, metadata, "analysis_type"))
    if cli_analysis and input_analysis and cli_analysis != input_analysis:
        raise_invalid(
            "analysis_type_conflict",
            "CLI analysis conflicts with input analysis_type.",
            details={"cli_analysis": cli_analysis, "input_analysis": input_analysis},
        )
    return cli_analysis or input_analysis or "aggregate_delta_attribution"


def unique_strings(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def parse_dimensions(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return unique_strings(value.split(","))
    if isinstance(value, (list, tuple)):
        return unique_strings(value)
    return unique_strings([value])


def filter_columns(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        return unique_strings(value.keys())
    return parse_dimensions(value)


def selected_filter_columns(*values: Any) -> list[str]:
    columns: list[str] = []
    for value in values:
        columns.extend(filter_columns(value))
    return unique_strings(columns)


def summary_table_metadata(table_name: str) -> dict[str, Any] | None:
    table = SUMMARY_TABLE_CATALOG.get(str(table_name).strip())
    if table is None:
        return None
    return {
        "table": table["table"],
        "granularity": table["granularity"],
        "parent": table["parent"],
        "retained_dimensions": list(table["retained_dimensions"]),
    }


def validate_summary_table_support(
    table_name: str,
    grouped_dimensions: Any,
    *,
    scope: Any = None,
    filters: Any = None,
    applied_scope_filters: Any = None,
) -> dict[str, Any]:
    table_text = str(table_name).strip()
    requested_dimensions = parse_dimensions(grouped_dimensions)
    requested_filter_columns = selected_filter_columns(scope, filters, applied_scope_filters)
    table = SUMMARY_TABLE_CATALOG.get(table_text)
    retained_dimensions = set(table["retained_dimensions"]) if table else set()
    retained_filter_columns = retained_dimensions | SUMMARY_FILTER_ALWAYS_RETAINED

    unsupported_dimensions = [
        dimension for dimension in requested_dimensions if dimension not in retained_dimensions
    ]
    unsupported_filters = [
        column for column in requested_filter_columns if column not in retained_filter_columns
    ]
    limitations: list[str] = []
    if unsupported_dimensions:
        limitations.append("unsupported_summary_dimension_set")
    if unsupported_filters:
        limitations.append("unsupported_summary_filter")

    result = {
        "generator_name": SQL_GENERATOR_NAME,
        "generator_version": SQL_GENERATOR_VERSION,
        "selected_table": table_text,
        "summary_table_known": table is not None,
        "retained_dimensions": sorted(retained_dimensions),
        "grouped_dimensions": requested_dimensions,
        "scope_filter_columns": requested_filter_columns,
        "unsupported_grouped_dimensions": unsupported_dimensions,
        "unsupported_filter_columns": unsupported_filters,
        "limitations": limitations,
        "supported": table is not None and not limitations,
    }
    if table:
        result["granularity"] = table["granularity"]
        result["parent"] = table["parent"]
    elif requested_dimensions:
        result["unsupported_grouped_dimensions"] = requested_dimensions
    return result


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def column_identity(column: Any) -> dict[str, Any]:
    if isinstance(column, str):
        return {"name": column}
    if not isinstance(column, dict):
        return {"name": str(column)}

    name = column.get("name", column.get("column", column.get("column_name", "")))
    identity = {"name": str(name)}
    for source_key, target_key in (
        ("type", "type"),
        ("data_type", "type"),
        ("column_type", "type"),
        ("column_category", "column_category"),
        ("base_function", "base_function"),
        ("merge_function", "merge_function"),
        ("default_expr", "default_expr"),
    ):
        if source_key in column and target_key not in identity:
            identity[target_key] = column[source_key]
    return identity


def table_metadata_columns(table_metadata: dict[str, Any]) -> list[dict[str, Any]]:
    raw_columns = table_metadata.get("columns", [])
    if not isinstance(raw_columns, list):
        raw_columns = []
    return sorted(
        (column_identity(column) for column in raw_columns),
        key=lambda column: column.get("name", ""),
    )


def metadata_fingerprint_payload(
    table_metadata: dict[str, Any],
    *,
    selected_columns: Any = None,
    metadata_retrieval_identity: str | None = None,
    metadata_fixture_identity: str | None = None,
) -> dict[str, Any]:
    columns = table_metadata_columns(table_metadata)
    column_names = [column["name"] for column in columns if column.get("name")]
    selected = parse_dimensions(selected_columns) if selected_columns is not None else column_names
    source_identity = metadata_retrieval_identity or metadata_fixture_identity
    return {
        "table": table_metadata.get("table")
        or table_metadata.get("table_name")
        or table_metadata.get("name"),
        "database": table_metadata.get("database"),
        "is_summary_table": bool(table_metadata.get("is_summary_table", False)),
        "selected_columns": sorted(selected),
        "columns": columns,
        "metadata_retrieval_identity": metadata_retrieval_identity,
        "metadata_fixture_identity": metadata_fixture_identity,
        "metadata_source_identity": source_identity,
    }


def metadata_fingerprint(
    table_metadata: dict[str, Any],
    *,
    selected_columns: Any = None,
    metadata_retrieval_identity: str | None = None,
    metadata_fixture_identity: str | None = None,
) -> str:
    payload = metadata_fingerprint_payload(
        table_metadata,
        selected_columns=selected_columns,
        metadata_retrieval_identity=metadata_retrieval_identity,
        metadata_fixture_identity=metadata_fixture_identity,
    )
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def sha256_payload_digest(payload: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def is_sha256_digest(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.match(value) is not None


def decimal_value(value: Any, *, path: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise_invalid(
            "non_finite_digest_value",
            "Digest numeric fields must be finite decimal values.",
            path=path,
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise_invalid(
            "non_finite_digest_value",
            "Digest numeric fields must be finite decimal values.",
            path=path,
        )
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise_invalid(
            "non_finite_digest_value",
            "Digest numeric fields must be finite decimal values.",
            path=path,
        )
    if not number.is_finite():
        raise_invalid(
            "non_finite_digest_value",
            "Digest numeric fields must be finite decimal values.",
            path=path,
        )
    if number == Decimal("-0"):
        return Decimal("0")
    return number


def digest_decimal(
    value: Any,
    *,
    path: str,
    places: int = 6,
) -> str:
    number = decimal_value(value, path=path)
    quant = Decimal("1").scaleb(-places)
    rounded = number.quantize(quant, rounding=ROUND_HALF_UP)
    if rounded == Decimal("-0").quantize(quant):
        rounded = Decimal("0").quantize(quant)
    return f"{rounded:.{places}f}"


def digest_support_value(value: Any, *, path: str) -> int | str:
    number = decimal_value(value, path=path)
    if number < 0:
        raise_invalid(
            "non_finite_digest_value",
            "Digest support counts must be non-negative.",
            path=path,
        )
    if number == number.to_integral_value():
        return int(number)
    return digest_decimal(number, path=path)


def digest_percentage(value: Any, *, path: str) -> str:
    return digest_decimal(value, path=path, places=2)


def normalize_digest_timestamp(value: Any, *, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise_invalid(
            "timestamp_invalid",
            "Digest timestamps must be RFC 3339 strings with a timezone.",
            path=path,
        )
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise_invalid(
            "timestamp_invalid",
            "Digest timestamps must be RFC 3339 strings with a timezone.",
            path=path,
        )
    if parsed.tzinfo is None:
        raise_invalid(
            "timestamp_invalid",
            "Digest timestamps must include a deterministic timezone.",
            path=path,
        )
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_digest_window(value: Any, *, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise_invalid(
            "window_invalid",
            "Digest window fields must be objects with start and end.",
            path=path,
        )
    normalized = {
        "start": normalize_digest_timestamp(value.get("start"), path=f"{path}.start"),
        "end": normalize_digest_timestamp(value.get("end"), path=f"{path}.end"),
    }
    if "label" in value:
        normalized["label"] = None if value["label"] is None else str(value["label"])
    return normalized


def normalize_digest_value(value: Any, *, path: str) -> Any:
    if isinstance(value, dict):
        return {
            str(key): normalize_digest_value(value[key], path=f"{path}.{key}")
            for key in sorted(value)
        }
    if isinstance(value, list):
        return [
            normalize_digest_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float, Decimal)):
        return digest_decimal(value, path=path)
    return str(value)


def collect_trust_metadata(payload: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    trust_metadata: dict[str, Any] = {}
    for key in TRUST_METADATA_FIELDS:
        value = resolve_value(payload, metadata, key)
        if value is not None:
            trust_metadata[key] = value
    return trust_metadata


def canonical_digest_row(
    row: dict[str, Any],
    dimensions: list[str],
    *,
    path: str,
) -> dict[str, Any]:
    digest_row: dict[str, Any] = {
        "dimensions": {
            dimension: row["dimensions"].get(dimension)
            for dimension in dimensions
        },
        "current": None
        if row.get("current") is None
        else digest_decimal(row["current"], path=f"{path}.current"),
        "baseline": None
        if row.get("baseline") is None
        else digest_decimal(row["baseline"], path=f"{path}.baseline"),
    }
    for key in (
        "current_support_raw",
        "baseline_support_raw",
        "baseline_support_normalized",
    ):
        if key in row and row[key] is not None:
            digest_row[key] = digest_support_value(row[key], path=f"{path}.{key}")
    for key in (
        "baseline_raw",
        "absolute_delta",
        "abs_delta",
        "complete_scope_total_abs_delta",
        "baseline_normalization_factor",
    ):
        if key in row:
            digest_row[key] = None if row[key] is None else digest_decimal(row[key], path=f"{path}.{key}")
    for key in ("pct_change", "contribution_pct"):
        if key in row:
            digest_row[key] = None if row[key] is None else digest_percentage(row[key], path=f"{path}.{key}")
    return digest_row


def canonical_row_sort_key(row: dict[str, Any], dimensions: list[str]) -> tuple[Any, ...]:
    dimension_key = tuple(
        (row["dimensions"].get(dimension) is None, "" if row["dimensions"].get(dimension) is None else str(row["dimensions"].get(dimension)))
        for dimension in dimensions
    )
    return dimension_key + (canonical_json_bytes(row).decode("utf-8"),)


def digest_payload_v1_from_normalized(
    normalized: dict[str, Any],
    *,
    options: Any = None,
) -> dict[str, Any]:
    opts = normalize_options(options)
    limit_value = opts.get("limit")
    limit = int(limit_value) if limit_value is not None else 0
    report_metadata = normalized.get("report_metadata", {})
    trust_metadata = normalized.get("trust_metadata", {})
    rows = [
        canonical_digest_row(row, normalized["dimensions"], path=f"$.canonical_rows[{index}]")
        for index, row in enumerate(normalized["canonical_rows"])
    ]
    rows.sort(key=lambda row: canonical_row_sort_key(row, normalized["dimensions"]))

    payload: dict[str, Any] = {
        "digest_schema_version": DIGEST_SCHEMA_VERSION,
        "metric": normalized["metric"],
        "metric_kind": normalized["metric_kind"],
        "dimensions": list(normalized["dimensions"]),
        "row_shape": normalized["row_shape"],
        "rowset_complete": False,
        "contribution_basis": "none",
        "source_limit_applied": bool(report_metadata.get("source_limit_applied", False)),
        "output_limit": limit,
        "output_limit_applied": bool(limit > 0 and len(rows) > limit),
        "mapped_rows": rows,
    }
    input_assertions = normalized.get("input_assertions", {})
    if "complete_scope_total_abs_delta" in input_assertions:
        value = input_assertions["complete_scope_total_abs_delta"]
        payload["complete_scope_total_abs_delta"] = (
            None
            if value is None
            else digest_decimal(value, path="$.complete_scope_total_abs_delta")
        )
    for key in (
        "scope",
        "filters",
        "applied_scope_filters",
        "granularity",
        "comparison_type",
    ):
        if key in report_metadata:
            payload[key] = normalize_digest_value(report_metadata[key], path=f"$.{key}")
    if "current_window" in report_metadata:
        payload["current_window"] = normalize_digest_window(
            report_metadata["current_window"],
            path="$.current_window",
        )
    if "baseline_windows" in normalized:
        payload["baseline_windows"] = [
            normalize_digest_window(window, path=f"$.baseline_windows[{index}]")
            for index, window in enumerate(normalized["baseline_windows"])
        ]
    if "baseline_method" in normalized:
        payload["baseline_method"] = normalized["baseline_method"]
    if "baseline_value_semantic" in normalized:
        payload["baseline_value_semantic"] = normalized["baseline_value_semantic"]
    if "baseline_normalization" in trust_metadata:
        payload["baseline_normalization"] = normalize_digest_value(
            trust_metadata["baseline_normalization"],
            path="$.baseline_normalization",
        )
    for source_key, target_key in (
        ("table_used", "selected_table"),
        ("selected_table", "selected_table"),
        ("selected_columns", "selected_columns"),
        ("metadata_origin", "metadata_origin"),
        ("metadata_fingerprint", "metadata_fingerprint"),
        ("metadata_retrieval_identity", "metadata_retrieval_identity"),
        ("metadata_fixture_identity", "metadata_fixture_identity"),
        ("merge_expressions", "merge_expressions"),
        ("limit_stage", "limit_stage"),
        ("source_limit_stage", "source_limit_stage"),
        ("query_fingerprint", "query_fingerprint"),
        ("template_id", "template_id"),
    ):
        source = report_metadata if source_key == "table_used" else trust_metadata
        if source_key in source and source[source_key] is not None:
            payload[target_key] = normalize_digest_value(
                source[source_key],
                path=f"$.{target_key}",
            )
    return payload


def result_digest_v1(input_doc: Any, *, options: Any = None) -> str:
    normalized = normalize_input_rows(input_doc, options=options)
    return sha256_payload_digest(digest_payload_v1_from_normalized(normalized, options=options))


TRUSTED_EVIDENCE_TYPES = {
    "complete_scope_pre_limit_evidence",
    "zero_fill_evidence",
    "provided_contribution_evidence",
    "complete_rowset_evidence",
    "raw_fallback_coverage_evidence",
    "duplicate_aggregation_evidence",
}


def normalized_contract_for_trust(
    normalized: dict[str, Any],
    digest_payload: dict[str, Any],
) -> dict[str, Any]:
    contract = {
        "metric": normalized["metric"],
        "dimensions": list(normalized["dimensions"]),
        "grouped_dimensions": list(normalized["dimensions"]),
        "scope": digest_payload.get("scope", {}),
        "applied_scope_filters": digest_payload.get("applied_scope_filters"),
        "current_window": digest_payload.get("current_window"),
        "baseline_windows": digest_payload.get("baseline_windows"),
        "baseline_method": digest_payload.get("baseline_method"),
        "baseline_value_semantic": digest_payload.get("baseline_value_semantic"),
        "baseline_normalization": digest_payload.get("baseline_normalization"),
        "selected_table": digest_payload.get("selected_table"),
        "selected_columns": digest_payload.get("selected_columns"),
        "metadata_origin": digest_payload.get("metadata_origin"),
        "metadata_fingerprint": digest_payload.get("metadata_fingerprint"),
        "metadata_retrieval_identity": digest_payload.get("metadata_retrieval_identity"),
        "merge_expressions": digest_payload.get("merge_expressions"),
        "limit_stage": digest_payload.get("limit_stage"),
        "template_id": digest_payload.get("template_id", SQL_TEMPLATE_ID),
        "query_fingerprint": digest_payload.get("query_fingerprint"),
    }
    return {key: value for key, value in contract.items() if value is not None}


REQUIRED_TRUST_CONTRACT_FIELDS = (
    "metric",
    "dimensions",
    "grouped_dimensions",
    "scope",
    "current_window",
    "baseline_windows",
    "baseline_method",
    "baseline_value_semantic",
    "baseline_normalization",
    "selected_table",
    "selected_columns",
    "metadata_origin",
    "metadata_fingerprint",
    "metadata_retrieval_identity",
    "merge_expressions",
    "limit_stage",
    "template_id",
    "query_fingerprint",
)


def trust_report_selector_matches(
    applies_to: Any,
    normalized: dict[str, Any],
) -> bool:
    if applies_to == {"scope": "report"}:
        return True
    if not isinstance(applies_to, dict):
        return False
    row_key = applies_to.get("row_key")
    if not isinstance(row_key, dict):
        return False
    dimensions = normalized["dimensions"]
    if sorted(row_key) != sorted(dimensions):
        return False
    normalized_row_key = {dimension: normalize_dimension_value(row_key.get(dimension)) for dimension in dimensions}
    return any(row["dimensions"] == normalized_row_key for row in normalized["canonical_rows"])


def values_match(left: Any, right: Any) -> bool:
    try:
        return normalize_digest_value(left, path="$.left") == normalize_digest_value(right, path="$.right")
    except InvalidInputError:
        return False


def append_once(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def validate_common_evidence_fields(
    evidence: dict[str, Any],
    *,
    context: dict[str, Any],
    normalized: dict[str, Any],
    contract: dict[str, Any],
    reasons: list[str],
) -> bool:
    valid = True
    if evidence.get("evidence_source") != TRUSTED_EVIDENCE_SOURCE:
        append_once(reasons, "trusted_evidence_mismatch")
        valid = False
    if evidence.get("generator_name") != SQL_GENERATOR_NAME:
        append_once(reasons, "trusted_evidence_mismatch")
        valid = False
    if evidence.get("generator_version") != SQL_GENERATOR_VERSION:
        append_once(reasons, "trusted_evidence_mismatch")
        valid = False
    if evidence.get("template_id") != contract.get("template_id"):
        append_once(reasons, "trusted_evidence_mismatch")
        valid = False

    if not is_sha256_digest(evidence.get("query_fingerprint")) or evidence.get("query_fingerprint") != context.get("query_fingerprint"):
        append_once(reasons, "trusted_evidence_mismatch")
        if evidence.get("query_fingerprint") in (None, ""):
            append_once(reasons, "query_fingerprint_missing")
        valid = False
    if not is_sha256_digest(evidence.get("result_digest")) or evidence.get("result_digest") != context.get("result_digest"):
        append_once(reasons, "trusted_evidence_mismatch")
        if evidence.get("result_digest") in (None, ""):
            append_once(reasons, "result_digest_missing")
        valid = False
    if not trust_report_selector_matches(evidence.get("applies_to"), normalized):
        append_once(reasons, "trusted_evidence_mismatch")
        valid = False
    for key in REQUIRED_TRUST_CONTRACT_FIELDS:
        if key not in contract or key not in evidence or not values_match(evidence[key], contract[key]):
            append_once(reasons, "trusted_evidence_mismatch")
            valid = False
    for key in (
        "scope_matches_report",
        "windows_match_report",
        "baseline_method_matches_report",
    ):
        if evidence.get(key) is not True:
            append_once(reasons, "trusted_evidence_mismatch")
            valid = False
    return valid


def trusted_decimal(value: Any, *, path: str) -> Decimal | None:
    try:
        return decimal_value(value, path=path)
    except InvalidInputError:
        return None


def digest_decimal_matches(left: Decimal, right: Decimal, *, places: int = 6) -> bool:
    try:
        return digest_decimal(left, path="$.left", places=places) == digest_decimal(right, path="$.right", places=places)
    except InvalidInputError:
        return False


def validate_provided_contribution_values(
    evidence: dict[str, Any],
    normalized: dict[str, Any],
    reasons: list[str],
) -> bool:
    valid = True
    tolerance = PROVIDED_CONTRIBUTION_TOLERANCE_PP
    if "contribution_pct_tolerance_pp" in evidence:
        evidence_tolerance = trusted_decimal(
            evidence.get("contribution_pct_tolerance_pp"),
            path="$.trusted_evidence.contribution_pct_tolerance_pp",
        )
        if evidence_tolerance is None or evidence_tolerance < 0:
            append_once(reasons, "provided_contribution_inconsistent")
            valid = False
        elif evidence_tolerance > PROVIDED_CONTRIBUTION_TOLERANCE_PP:
            append_once(reasons, "provided_contribution_inconsistent")
            valid = False
        else:
            tolerance = evidence_tolerance

    if evidence.get("contribution_pct_field") != "contribution_pct":
        append_once(reasons, "provided_contribution_inconsistent")
        valid = False
    if evidence.get("denominator_field") != "complete_scope_total_abs_delta":
        append_once(reasons, "provided_contribution_inconsistent")
        valid = False
    if not isinstance(evidence.get("denominator_expression"), str) or not evidence.get("denominator_expression").strip():
        append_once(reasons, "provided_contribution_inconsistent")
        valid = False
    if evidence.get("pre_denominator_filter_applied") is not False:
        append_once(reasons, "provided_contribution_inconsistent")
        valid = False
    if evidence.get("metric_semantics_reviewed") is not True:
        append_once(reasons, "provided_contribution_inconsistent")
        valid = False
    if evidence.get("reviewed_metric_kind") != normalized.get("metric_kind"):
        append_once(reasons, "provided_contribution_inconsistent")
        valid = False
    if normalized.get("metric_kind") != "additive_count":
        append_once(reasons, "provided_contribution_inconsistent")
        valid = False

    denominators: list[Decimal] = []
    for index, row in enumerate(normalized["canonical_rows"]):
        denominator = trusted_decimal(
            row.get("complete_scope_total_abs_delta"),
            path=f"$.canonical_rows[{index}].complete_scope_total_abs_delta",
        )
        if denominator is None or denominator < 0:
            append_once(reasons, "provided_contribution_inconsistent")
            valid = False
            continue
        denominators.append(denominator)

        contribution_present = "contribution_pct" in row
        contribution = trusted_decimal(
            row.get("contribution_pct"),
            path=f"$.canonical_rows[{index}].contribution_pct",
        ) if contribution_present and row.get("contribution_pct") is not None else None
        if denominator == 0:
            if contribution_present and row.get("contribution_pct") is not None:
                append_once(reasons, "provided_contribution_inconsistent")
                valid = False
            continue
        if contribution is None:
            append_once(reasons, "provided_contribution_inconsistent")
            valid = False
            continue
        rounded_contribution = trusted_decimal(
            digest_percentage(contribution, path=f"$.canonical_rows[{index}].contribution_pct"),
            path=f"$.canonical_rows[{index}].contribution_pct",
        )
        if rounded_contribution is None or rounded_contribution < 0 or rounded_contribution > 100:
            append_once(reasons, "provided_contribution_inconsistent")
            valid = False
            continue

        current = trusted_decimal(row.get("current"), path=f"$.canonical_rows[{index}].current")
        baseline = trusted_decimal(row.get("baseline"), path=f"$.canonical_rows[{index}].baseline")
        if current is None or baseline is None:
            append_once(reasons, "provided_contribution_inconsistent")
            valid = False
            continue
        absolute_delta = current - baseline
        if "absolute_delta" in row and row["absolute_delta"] is not None:
            supplied_absolute_delta = trusted_decimal(
                row["absolute_delta"],
                path=f"$.canonical_rows[{index}].absolute_delta",
            )
            if supplied_absolute_delta is None or not digest_decimal_matches(supplied_absolute_delta, absolute_delta):
                append_once(reasons, "provided_contribution_inconsistent")
                valid = False
        if "abs_delta" in row and row["abs_delta"] is not None:
            supplied_abs_delta = trusted_decimal(row["abs_delta"], path=f"$.canonical_rows[{index}].abs_delta")
            if supplied_abs_delta is None or not digest_decimal_matches(supplied_abs_delta, abs(absolute_delta)):
                append_once(reasons, "provided_contribution_inconsistent")
                valid = False

        expected = abs(absolute_delta) / denominator * Decimal("100")
        if abs(rounded_contribution - expected) > tolerance:
            append_once(reasons, "provided_contribution_inconsistent")
            valid = False

    if not denominators:
        append_once(reasons, "provided_contribution_inconsistent")
        valid = False
    elif any(not digest_decimal_matches(denominator, denominators[0]) for denominator in denominators[1:]):
        append_once(reasons, "provided_contribution_inconsistent")
        valid = False

    top_level_denominator = normalized.get("input_assertions", {}).get("complete_scope_total_abs_delta")
    if top_level_denominator is not None and denominators:
        top_level = trusted_decimal(top_level_denominator, path="$.complete_scope_total_abs_delta")
        if top_level is None or not digest_decimal_matches(top_level, denominators[0]):
            append_once(reasons, "provided_contribution_inconsistent")
            valid = False

    return valid


def validate_specific_evidence_fields(
    evidence: dict[str, Any],
    *,
    normalized: dict[str, Any],
    reasons: list[str],
) -> bool:
    evidence_type = evidence.get("evidence_type")
    valid = True
    if evidence_type == "complete_scope_pre_limit_evidence":
        required_true = (
            "computed_over_complete_grouped_scope",
            "computed_before_output_limit",
            "denominator_scope_matches_report",
        )
        if any(evidence.get(key) is not True for key in required_true):
            append_once(reasons, "trusted_evidence_mismatch")
            valid = False
        if evidence.get("denominator_basis") != "sum_abs_delta":
            append_once(reasons, "trusted_evidence_mismatch")
            valid = False
        if not isinstance(evidence.get("denominator_expression"), str) or not evidence.get("denominator_expression").strip():
            append_once(reasons, "trusted_evidence_mismatch")
            valid = False
        if evidence.get("source_limit_applied_before_denominator") is True:
            append_once(reasons, "trusted_evidence_mismatch")
            valid = False
    elif evidence_type == "zero_fill_evidence":
        period_value_trust = evidence.get("period_value_trust")
        if not isinstance(period_value_trust, dict) or not {
            "current",
            "baseline",
        }.issubset(period_value_trust):
            append_once(reasons, "trusted_evidence_mismatch")
            valid = False
        elif any(
            period_value_trust[side] not in {"complete_grouped_scope", "trusted_full_scope_join"}
            for side in ("current", "baseline")
        ):
            append_once(reasons, "trusted_evidence_mismatch")
            valid = False
        if not (evidence.get("grouped_scope_complete") is True or evidence.get("full_scope_joined_grouped_rowset") is True):
            append_once(reasons, "trusted_evidence_mismatch")
            valid = False
        if evidence.get("computed_before_output_limit") is not True:
            append_once(reasons, "trusted_evidence_mismatch")
            valid = False
    elif evidence_type == "provided_contribution_evidence":
        required_true = (
            "denominator_scope_matches_report",
            "computed_over_complete_grouped_scope",
            "computed_before_output_limit",
            "per_row_contribution",
        )
        if any(evidence.get(key) is not True for key in required_true):
            append_once(reasons, "provided_contribution_inconsistent")
            valid = False
        if evidence.get("source_limit_applied_before_denominator") is True:
            append_once(reasons, "provided_contribution_inconsistent")
            valid = False
        if evidence.get("denominator_basis") != "sum_abs_delta":
            append_once(reasons, "provided_contribution_inconsistent")
            valid = False
        if not isinstance(evidence.get("contribution_pct_field"), str) or not evidence.get("contribution_pct_field").strip():
            append_once(reasons, "provided_contribution_inconsistent")
            valid = False
        if not isinstance(evidence.get("denominator_field"), str) or not evidence.get("denominator_field").strip():
            append_once(reasons, "provided_contribution_inconsistent")
            valid = False
        if not validate_provided_contribution_values(evidence, normalized, reasons):
            valid = False
    elif evidence_type == "complete_rowset_evidence":
        if evidence.get("grouped_scope_complete") is not True or evidence.get("all_grouped_rows_returned") is not True:
            append_once(reasons, "trusted_evidence_mismatch")
            valid = False
    elif evidence_type == "raw_fallback_coverage_evidence":
        if evidence.get("coverage_reviewed") is not True and evidence.get("raw_fallback_coverage_reviewed") is not True:
            append_once(reasons, "trusted_evidence_mismatch")
            valid = False
    elif evidence_type == "duplicate_aggregation_evidence":
        if evidence.get("aggregation_allowed") is not True:
            append_once(reasons, "duplicate_aggregation_not_trusted")
            valid = False
        if evidence.get("partition_semantics") != "disjoint_source_partitions":
            append_once(reasons, "duplicate_aggregation_not_trusted")
            valid = False
        if not isinstance(evidence.get("partition_fields"), list) or not evidence.get("partition_fields"):
            append_once(reasons, "duplicate_aggregation_not_trusted")
            valid = False
    return valid


def validate_trusted_context(
    trusted_context: Any,
    normalized: dict[str, Any],
    digest_payload: dict[str, Any],
    recomputed_digest: str,
) -> dict[str, Any]:
    reasons: list[str] = []
    evidence_types: list[str] = []
    if trusted_context is None:
        return {
            "valid": False,
            "trusted": False,
            "result_digest": recomputed_digest,
            "reasons": ["trusted_context_missing"],
            "evidence_types": evidence_types,
        }
    if not isinstance(trusted_context, dict):
        return {
            "valid": False,
            "trusted": False,
            "result_digest": recomputed_digest,
            "reasons": ["trusted_context_invalid", "trusted_wrapper_unavailable"],
            "evidence_types": evidence_types,
        }

    context = trusted_context
    for key in ("query_fingerprint", "result_digest"):
        if not is_sha256_digest(context.get(key)):
            append_once(reasons, f"{key}_missing")
    if is_sha256_digest(context.get("result_digest")) and context["result_digest"] != recomputed_digest:
        append_once(reasons, "trusted_context_digest_mismatch")

    required_text_fields = (
        "generator_name",
        "generator_version",
        "wrapper_name",
        "wrapper_version",
        "template_id",
        "result_origin",
        "metadata_origin",
        "selected_table",
        "metadata_fingerprint",
        "metadata_retrieval_identity",
    )
    for key in required_text_fields:
        if not isinstance(context.get(key), str) or not context.get(key).strip():
            append_once(reasons, "trusted_context_invalid")
    if context.get("trusted_generator_invocation") is not True:
        append_once(reasons, "trusted_context_invalid")
    if context.get("generator_name") != SQL_GENERATOR_NAME:
        append_once(reasons, "trusted_context_invalid")
    if context.get("generator_version") != SQL_GENERATOR_VERSION:
        append_once(reasons, "trusted_context_invalid")
    if context.get("wrapper_name") != TRUSTED_WRAPPER_NAME:
        append_once(reasons, "trusted_context_invalid")
    if context.get("wrapper_version") != TRUSTED_WRAPPER_VERSION:
        append_once(reasons, "trusted_context_invalid")
    if context.get("result_origin") != TRUSTED_RESULT_ORIGIN:
        append_once(reasons, "trusted_context_invalid")
    if context.get("metadata_origin") != TRUSTED_METADATA_ORIGIN:
        append_once(reasons, "trusted_context_invalid")
    expected_template = digest_payload.get("template_id", SQL_TEMPLATE_ID)
    if context.get("template_id") != expected_template:
        append_once(reasons, "trusted_context_invalid")
    if not isinstance(context.get("selected_columns"), list) or not context.get("selected_columns"):
        append_once(reasons, "trusted_context_invalid")
    if not isinstance(context.get("merge_expressions"), dict):
        append_once(reasons, "trusted_context_invalid")

    contract = normalized_contract_for_trust(normalized, digest_payload)
    for key in REQUIRED_TRUST_CONTRACT_FIELDS:
        if key not in contract:
            append_once(reasons, "trusted_context_invalid")
    for key in (
        "selected_table",
        "selected_columns",
        "metadata_origin",
        "metadata_fingerprint",
        "metadata_retrieval_identity",
        "merge_expressions",
        "query_fingerprint",
        "template_id",
    ):
        if key in contract and (key not in context or not values_match(context[key], contract[key])):
            append_once(reasons, "trusted_context_invalid")

    trusted_evidence = context.get("trusted_evidence")
    if not isinstance(trusted_evidence, list):
        append_once(reasons, "trusted_evidence_missing")
        trusted_evidence = []
    elif not trusted_evidence:
        append_once(reasons, "trusted_evidence_missing")

    seen_ids: set[str] = set()
    valid_evidence_count = 0
    for item in trusted_evidence:
        if not isinstance(item, dict):
            append_once(reasons, "trusted_evidence_mismatch")
            continue
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            append_once(reasons, "trusted_evidence_mismatch")
        elif evidence_id in seen_ids:
            append_once(reasons, "trusted_evidence_mismatch")
        else:
            seen_ids.add(evidence_id)
        evidence_type = item.get("evidence_type")
        if evidence_type not in TRUSTED_EVIDENCE_TYPES:
            append_once(reasons, "trusted_evidence_mismatch")
            continue
        evidence_types.append(str(evidence_type))
        common_valid = validate_common_evidence_fields(
            item,
            context=context,
            normalized=normalized,
            contract=contract,
            reasons=reasons,
        )
        specific_valid = validate_specific_evidence_fields(item, normalized=normalized, reasons=reasons)
        if common_valid and specific_valid:
            valid_evidence_count += 1

    context_valid = not any(
        reason
        in {
            "trusted_context_invalid",
            "trusted_context_digest_mismatch",
            "query_fingerprint_missing",
            "result_digest_missing",
        }
        for reason in reasons
    )
    evidence_valid = valid_evidence_count > 0 and not {
        "trusted_evidence_mismatch",
        "provided_contribution_inconsistent",
    }.intersection(reasons)
    if not TRUSTED_WRAPPER_AVAILABLE:
        append_once(reasons, "trusted_wrapper_unavailable")
    if "duplicate_aggregation_evidence" in evidence_types and not TRUSTED_WRAPPER_AVAILABLE:
        append_once(reasons, "duplicate_aggregation_not_trusted")
    return {
        "valid": bool(context_valid and evidence_valid),
        "trusted": bool(context_valid and evidence_valid and TRUSTED_WRAPPER_AVAILABLE),
        "result_digest": recomputed_digest,
        "reasons": reasons,
        "evidence_types": sorted(set(evidence_types)),
    }


def normalize_metric_name(name: str) -> str | None:
    text = str(name).strip()
    if not text:
        return None
    return METRIC_ALIAS_TO_CANONICAL.get(text)


def metric_entry(metric_name: str) -> dict[str, Any]:
    canonical = normalize_metric_name(metric_name)
    if canonical is None:
        raise_invalid(
            "unsupported_metric",
            f"Metric '{metric_name}' is not in the reviewed v1 allowlist.",
            details={"metric": metric_name},
        )
    entry = dict(METRIC_ALLOWLIST[canonical])
    entry["name"] = canonical
    return entry


def metric_aliases(metric_name: str) -> tuple[str, ...]:
    entry = metric_entry(metric_name)
    aliases = [entry["name"], *entry["aliases"]]
    return tuple(dict.fromkeys(alias for alias in aliases if alias))


def current_metric_keys(metric_name: str) -> tuple[str, ...]:
    keys = ["current"]
    for alias in metric_aliases(metric_name):
        keys.extend((f"current_{alias}", f"{alias}_current", f"current.{alias}"))
    return tuple(dict.fromkeys(keys))


def baseline_metric_keys(metric_name: str) -> tuple[str, ...]:
    keys = ["baseline"]
    for alias in metric_aliases(metric_name):
        keys.extend((f"baseline_{alias}", f"{alias}_baseline", f"baseline.{alias}"))
    return tuple(dict.fromkeys(keys))


def period_metric_keys(metric_name: str) -> tuple[str, ...]:
    return tuple(
        alias
        for alias in metric_aliases(metric_name)
        if not alias.startswith(("current_", "baseline_"))
        and not alias.endswith(("_current", "_baseline"))
    )


def sql_identifier(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def sql_string_literal(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return sql_string_literal(value)


def sql_table_name(table_metadata: dict[str, Any]) -> str:
    table = table_metadata.get("table") or table_metadata.get("table_name") or table_metadata.get("name")
    if not isinstance(table, str) or not table.strip():
        raise_invalid(
            "table_metadata_missing_table",
            "Selected table metadata must include a non-blank table name.",
        )
    database = table_metadata.get("database")
    if isinstance(database, str) and database.strip():
        return f"{sql_identifier(database.strip())}.{sql_identifier(table.strip())}"
    return sql_identifier(table.strip())


def table_metadata_table_name(table_metadata: dict[str, Any]) -> str:
    table = table_metadata.get("table") or table_metadata.get("table_name") or table_metadata.get("name")
    if not isinstance(table, str) or not table.strip():
        raise_invalid(
            "table_metadata_missing_table",
            "Selected table metadata must include a non-blank table name.",
        )
    return table.strip()


def column_lookup(table_metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for column in table_metadata_columns(table_metadata):
        name = column.get("name")
        if not isinstance(name, str) or not name:
            continue
        lookup[name] = column
        lookup.setdefault(name.lower(), column)
    return lookup


def metric_column_candidates(metric_name: str) -> list[str]:
    candidates: list[str] = []
    for alias in metric_aliases(metric_name):
        candidates.extend(
            (
                alias,
                f"sum({alias})",
                f"sumIf({alias})",
                f"count({alias})",
            )
        )
    if normalize_metric_name(metric_name) == "requests":
        candidates.extend(("count()", "count"))
    return unique_strings(candidates)


def support_column_candidates(metric_name: str) -> list[str]:
    if metric_support_uses_metric_value(metric_entry(metric_name)["metric_kind"]):
        return metric_column_candidates(metric_name)
    return metric_column_candidates("requests")


def find_metadata_column(
    table_metadata: dict[str, Any],
    candidates: Iterable[str],
    *,
    purpose: str,
) -> dict[str, Any]:
    lookup = column_lookup(table_metadata)
    for candidate in candidates:
        column = lookup.get(candidate) or lookup.get(candidate.lower())
        if column is not None:
            return column
    raise_invalid(
        "metadata_column_missing",
        f"Hydrolix metadata does not expose a reviewed {purpose} column.",
        details={"purpose": purpose, "candidates": list(candidates)},
    )


def aggregate_sql_expression(column: dict[str, Any]) -> str:
    name = str(column.get("name", "")).strip()
    if not name:
        raise_invalid("metadata_column_missing", "Hydrolix metadata column name is blank.")
    category = column.get("column_category")
    if category == "AggregateColumn":
        merge_function = column.get("merge_function")
        if not isinstance(merge_function, str) or not merge_function.strip():
            raise_invalid(
                "metadata_merge_function_missing",
                f"Aggregate-state column '{name}' is missing merge_function metadata.",
                details={"column": name},
            )
        return f"{merge_function.strip()}({sql_identifier(name)})"
    if category == "SummaryColumn":
        return sql_identifier(name)
    return f"sum({sql_identifier(name)})"


def merge_expression_map(columns: Iterable[dict[str, Any]]) -> dict[str, str]:
    expressions: dict[str, str] = {}
    for column in columns:
        if column.get("column_category") != "AggregateColumn":
            continue
        name = str(column.get("name", "")).strip()
        if not name:
            continue
        expressions[name] = aggregate_sql_expression(column)
    return expressions


def required_metadata_column(
    table_metadata: dict[str, Any],
    column_name: str,
    *,
    purpose: str,
) -> dict[str, Any]:
    return find_metadata_column(table_metadata, [column_name], purpose=purpose)


def normalize_window(window: Any, *, path: str) -> dict[str, Any]:
    if not isinstance(window, dict):
        raise_invalid(
            "window_invalid",
            "SQL template windows must be objects with start and end.",
            path=path,
        )
    start = window.get("start")
    end = window.get("end")
    if not isinstance(start, str) or not start.strip() or not isinstance(end, str) or not end.strip():
        raise_invalid(
            "window_invalid",
            "SQL template windows must include non-blank start and end strings.",
            path=path,
        )
    normalized = {"start": start.strip(), "end": end.strip()}
    if "label" in window and window["label"] is not None:
        normalized["label"] = str(window["label"])
    return normalized


def normalize_baseline_windows(windows: Any) -> list[dict[str, Any]]:
    if not isinstance(windows, list) or not windows:
        raise_invalid(
            "baseline_windows_invalid",
            "SQL template rendering requires at least one baseline window.",
            path="$.baseline_windows",
        )
    return [normalize_window(window, path=f"$.baseline_windows[{index}]") for index, window in enumerate(windows)]


def normalized_predicate_value(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return sorted(
            (normalize_digest_value(item, path="$.predicate") for item in value),
            key=lambda item: canonical_json_bytes(item).decode("utf-8"),
        )
    return normalize_digest_value(value, path="$.predicate")


def merge_sql_predicate_maps(*values: Any) -> dict[str, Any]:
    predicates: dict[str, Any] = {}
    normalized_by_column: dict[str, Any] = {}
    for value in values:
        if value is None:
            continue
        if not isinstance(value, dict):
            raise_invalid(
                "scope_invalid",
                "SQL template scope and filter predicates must be objects of retained column predicates.",
            )
        for key in sorted(value):
            column = str(key).strip()
            if not column:
                raise_invalid(
                    "scope_invalid",
                    "SQL template scope and filter predicate columns must be non-blank.",
                )
            predicate_value = value[key]
            if isinstance(predicate_value, (list, tuple)) and not predicate_value:
                raise_invalid(
                    "scope_invalid",
                    "SQL template scope list predicates must not be empty.",
                    details={"column": key},
                )
            normalized = normalized_predicate_value(predicate_value)
            if column in normalized_by_column:
                if normalized_by_column[column] != normalized:
                    raise_invalid(
                        "scope_filter_conflict",
                        "SQL template scope and filters contain conflicting predicates for the same column.",
                        details={"column": column},
                    )
                continue
            normalized_by_column[column] = normalized
            predicates[column] = predicate_value
    return predicates


def sql_scope_predicates(scope: Any) -> list[str]:
    if scope is None:
        return []
    if not isinstance(scope, dict):
        raise_invalid(
            "scope_invalid",
            "SQL template scope must be an object of retained column predicates.",
        )
    predicates: list[str] = []
    for key in sorted(scope):
        value = scope[key]
        column = sql_identifier(str(key))
        if isinstance(value, (list, tuple)):
            if not value:
                raise_invalid(
                    "scope_invalid",
                    "SQL template scope list predicates must not be empty.",
                    details={"column": key},
                )
            predicates.append(f"{column} IN ({', '.join(sql_literal(item) for item in value)})")
        elif value is None:
            predicates.append(f"{column} IS NULL")
        else:
            predicates.append(f"{column} = {sql_literal(value)}")
    return predicates


def selected_sql_columns(
    *,
    time_column: str,
    dimensions: list[str],
    scope: Any,
    filters: Any,
    applied_scope_filters: Any,
    metric_column: dict[str, Any],
    support_column: dict[str, Any],
) -> list[str]:
    columns = [time_column, *dimensions]
    columns.extend(selected_filter_columns(scope, filters, applied_scope_filters))
    columns.append(str(metric_column["name"]))
    columns.append(str(support_column["name"]))
    return unique_strings(columns)


def render_select_dimensions(alias: str, dimensions: list[str]) -> list[str]:
    return [f"{alias}.{sql_identifier(dimension)} AS {sql_identifier(dimension)}" for dimension in dimensions]


def render_group_by_dimensions(dimensions: list[str]) -> str:
    return ", ".join(sql_identifier(dimension) for dimension in dimensions)


def render_join_key(dimensions: list[str]) -> str:
    return ", ".join(sql_identifier(dimension) for dimension in dimensions)


def render_coalesced_dimensions(dimensions: list[str]) -> list[str]:
    return [
        f"coalesce(c.{sql_identifier(dimension)}, b.{sql_identifier(dimension)}) AS {sql_identifier(dimension)}"
        for dimension in dimensions
    ]


def baseline_reduction_expression(
    *,
    baseline_method: str,
    source_column: str,
    duration_column: str,
) -> str:
    if baseline_method == "mean_of_baseline_windows":
        return f"avg({source_column})"
    if baseline_method == "duration_weighted_mean_of_baseline_windows":
        return f"sum({source_column})"
    return f"sum({source_column})"


def query_fingerprint(payload: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def render_attribution_sql_template(
    *,
    table_metadata: dict[str, Any],
    metric: str,
    dimensions: Any,
    scope: Any,
    current_window: Any,
    baseline_windows: Any,
    baseline_method: str,
    output_limit: int,
    source_limit_applied: bool = False,
    source_limit_stage: str = "none",
    filters: Any = None,
    applied_scope_filters: Any = None,
    metadata_origin: str = "direct_hydrolix_table_metadata",
    metadata_retrieval_identity: str | None = None,
    metadata_fixture_identity: str | None = None,
    time_column: str = "timestamp",
    baseline_value_semantic: str = "duration_normalized_to_current_window",
) -> dict[str, Any]:
    """Render reviewed summary-table attribution SQL and assertion provenance."""

    table_name = table_metadata_table_name(table_metadata)
    if table_metadata.get("is_summary_table") is not True:
        raise_invalid(
            "table_metadata_not_summary_table",
            "SQL template rendering requires Hydrolix metadata for a summary table.",
            details={"table": table_name},
        )
    requested_dimensions = parse_dimensions(dimensions)
    if not requested_dimensions:
        raise_invalid(
            "dimensions_missing",
            "SQL template rendering requires at least one grouped dimension.",
        )
    if baseline_method not in BASELINE_METHODS or baseline_method == "externally_precomputed_baseline":
        raise_invalid(
            "baseline_method_invalid",
            f"Unsupported SQL template baseline_method '{baseline_method}'.",
        )
    if baseline_value_semantic not in BASELINE_VALUE_SEMANTICS or baseline_value_semantic == "externally_precomputed_baseline":
        raise_invalid(
            "baseline_value_semantic_invalid",
            f"Unsupported SQL template baseline_value_semantic '{baseline_value_semantic}'.",
        )
    if source_limit_stage not in SQL_LIMIT_STAGES:
        raise_invalid(
            "limit_stage_invalid",
            f"Unsupported source_limit_stage '{source_limit_stage}'.",
        )
    try:
        limit = int(output_limit)
    except (TypeError, ValueError):
        raise_invalid("output_limit_invalid", "SQL template output_limit must be an integer.")
    if limit <= 0:
        raise_invalid("output_limit_invalid", "SQL template output_limit must be positive.")

    metric_info = metric_entry(metric)
    metric_column = find_metadata_column(
        table_metadata,
        metric_column_candidates(metric_info["name"]),
        purpose="metric",
    )
    support_column = find_metadata_column(
        table_metadata,
        support_column_candidates(metric_info["name"]),
        purpose="support",
    )
    required_metadata_column(table_metadata, time_column, purpose="time")
    for dimension in requested_dimensions:
        required_metadata_column(table_metadata, dimension, purpose="dimension")
    for filter_column in selected_filter_columns(scope, filters, applied_scope_filters):
        required_metadata_column(table_metadata, filter_column, purpose="scope/filter")

    summary_validation = validate_summary_table_support(
        table_name,
        requested_dimensions,
        scope=scope,
        filters=filters,
        applied_scope_filters=applied_scope_filters,
    )
    if not summary_validation["supported"]:
        raise_invalid(
            "unsupported_summary_selection",
            "Selected summary table does not retain the requested grouped dimensions or scope filters.",
            details=summary_validation,
        )

    current = normalize_window(current_window, path="$.current_window")
    baselines = normalize_baseline_windows(baseline_windows)
    selected_columns = selected_sql_columns(
        time_column=time_column,
        dimensions=requested_dimensions,
        scope=scope,
        filters=filters,
        applied_scope_filters=applied_scope_filters,
        metric_column=metric_column,
        support_column=support_column,
    )
    selected_column_metadata = [
        required_metadata_column(table_metadata, column, purpose="selected")
        for column in selected_columns
    ]
    merge_expressions = merge_expression_map((metric_column, support_column))
    metric_expression = aggregate_sql_expression(metric_column)
    support_expression = aggregate_sql_expression(support_column)
    metadata_hash = metadata_fingerprint(
        table_metadata,
        selected_columns=selected_columns,
        metadata_retrieval_identity=metadata_retrieval_identity,
        metadata_fixture_identity=metadata_fixture_identity,
    )
    sql_predicates = merge_sql_predicate_maps(scope, filters, applied_scope_filters)

    metric_name = metric_info["name"]
    current_metric_alias = f"current_{metric_name}"
    baseline_raw_alias = f"baseline_raw_{metric_name}"
    baseline_metric_alias = f"baseline_{metric_name}"
    dimension_group_by = render_group_by_dimensions(requested_dimensions)
    dimension_join_key = render_join_key(requested_dimensions)
    dimension_order = ", ".join(f"toString({sql_identifier(dimension)}) ASC" for dimension in requested_dimensions)
    current_predicates = [
        f"{sql_identifier(time_column)} >= current_start",
        f"{sql_identifier(time_column)} < current_end",
        *sql_scope_predicates(sql_predicates),
    ]

    with_lines = [
        f"  toDateTime({sql_string_literal(current['start'])}) AS current_start",
        f"  toDateTime({sql_string_literal(current['end'])}) AS current_end",
    ]
    baseline_ctes: list[str] = []
    baseline_union_parts: list[str] = []
    for index, baseline in enumerate(baselines, start=1):
        start_alias = f"baseline_start_{index}"
        end_alias = f"baseline_end_{index}"
        duration_alias = f"baseline_duration_seconds_{index}"
        with_lines.extend(
            (
                f"  toDateTime({sql_string_literal(baseline['start'])}) AS {start_alias}",
                f"  toDateTime({sql_string_literal(baseline['end'])}) AS {end_alias}",
                f"  dateDiff('second', {start_alias}, {end_alias}) AS {duration_alias}",
            )
        )
        baseline_predicates = [
            f"{sql_identifier(time_column)} >= {start_alias}",
            f"{sql_identifier(time_column)} < {end_alias}",
            *sql_scope_predicates(sql_predicates),
        ]
        baseline_ctes.append(
            "\n".join(
                [
                    f"  baseline_window_{index}_by_entity AS (",
                    "    SELECT",
                    *[f"      {sql_identifier(dimension)}," for dimension in requested_dimensions],
                    f"      {metric_expression} AS baseline_window_{metric_name},",
                    f"      {support_expression} AS baseline_window_support_raw,",
                    f"      {duration_alias} AS baseline_window_duration_seconds",
                    f"    FROM {sql_table_name(table_metadata)}",
                    "    WHERE " + "\n      AND ".join(baseline_predicates),
                    f"    GROUP BY {dimension_group_by}",
                    "  )",
                ]
            )
        )
        baseline_union_parts.append(f"SELECT * FROM baseline_window_{index}_by_entity")

    baseline_duration_terms = " + ".join(f"baseline_duration_seconds_{index}" for index in range(1, len(baselines) + 1))
    with_lines.extend(
        (
            "  dateDiff('second', current_start, current_end) AS current_duration_seconds",
            f"  {len(baselines)} AS baseline_window_count",
            f"  ({baseline_duration_terms}) AS baseline_total_duration_seconds",
        )
    )
    if baseline_value_semantic == "duration_normalized_to_current_window":
        if baseline_method == "mean_of_baseline_windows":
            with_lines.extend(
                (
                    "  toFloat64(baseline_total_duration_seconds) / nullIf(baseline_window_count, 0) AS baseline_average_duration_seconds",
                    "  toFloat64(current_duration_seconds) / nullIf(baseline_average_duration_seconds, 0) AS baseline_normalization_factor",
                )
            )
        else:
            with_lines.append(
                "  toFloat64(current_duration_seconds) / nullIf(baseline_total_duration_seconds, 0) AS baseline_normalization_factor"
            )
    else:
        with_lines.append("  toFloat64(1) AS baseline_normalization_factor")

    baseline_metric_reducer = baseline_reduction_expression(
        baseline_method=baseline_method,
        source_column=f"baseline_window_{metric_name}",
        duration_column="baseline_window_duration_seconds",
    )
    baseline_support_reducer = baseline_reduction_expression(
        baseline_method=baseline_method,
        source_column="baseline_window_support_raw",
        duration_column="baseline_window_duration_seconds",
    )
    baseline_union_sql = "\n    UNION ALL\n    ".join(baseline_union_parts)
    sql_lines = [
        "WITH",
        ",\n".join(with_lines) + ",",
        "  current_by_entity AS (",
        "    SELECT",
        *[f"      {sql_identifier(dimension)}," for dimension in requested_dimensions],
        f"      {metric_expression} AS {current_metric_alias},",
        f"      {support_expression} AS current_support_raw",
        f"    FROM {sql_table_name(table_metadata)}",
        "    WHERE " + "\n      AND ".join(current_predicates),
        f"    GROUP BY {dimension_group_by}",
        "  ),",
        *[cte + "," for cte in baseline_ctes],
        "  baseline_windows_by_entity AS (",
        f"    {baseline_union_sql}",
        "  ),",
        "  baseline_by_entity AS (",
        "    SELECT",
        *[f"      {sql_identifier(dimension)}," for dimension in requested_dimensions],
        f"      {baseline_metric_reducer} AS {baseline_raw_alias},",
        f"      {baseline_support_reducer} AS baseline_support_raw",
        "    FROM baseline_windows_by_entity",
        f"    GROUP BY {dimension_group_by}",
        "  ),",
        "  by_entity AS (",
        "    SELECT",
        *[f"      {line}," for line in render_coalesced_dimensions(requested_dimensions)],
        f"      coalesce(c.{current_metric_alias}, 0) AS {current_metric_alias},",
        f"      coalesce(b.{baseline_raw_alias}, 0) AS {baseline_raw_alias},",
        f"      coalesce(b.{baseline_raw_alias}, 0) * baseline_normalization_factor AS {baseline_metric_alias},",
        "      coalesce(c.current_support_raw, 0) AS current_support_raw,",
        "      coalesce(b.baseline_support_raw, 0) AS baseline_support_raw",
        "    FROM current_by_entity AS c",
        f"    FULL OUTER JOIN baseline_by_entity AS b USING ({dimension_join_key})",
        "  ),",
        "  scored AS (",
        "    SELECT",
        "      *,",
        f"      {current_metric_alias} - {baseline_metric_alias} AS absolute_delta,",
        f"      abs({current_metric_alias} - {baseline_metric_alias}) AS abs_delta,",
        f"      sum(abs({current_metric_alias} - {baseline_metric_alias})) OVER () AS complete_scope_total_abs_delta",
        "    FROM by_entity",
        "  )",
        "SELECT",
        *[f"  {sql_identifier(dimension)}," for dimension in requested_dimensions],
        f"  {current_metric_alias},",
        f"  {baseline_raw_alias},",
        f"  {baseline_metric_alias},",
        "  current_support_raw,",
        "  baseline_support_raw,",
        f"  {sql_string_literal(baseline_value_semantic)} AS baseline_value_semantic,",
        "  baseline_normalization_factor,",
        "  absolute_delta,",
        "  complete_scope_total_abs_delta,",
        f"  round(({current_metric_alias} - {baseline_metric_alias}) / greatest(abs({baseline_metric_alias}), 1.0) * 100, 6) AS pct_change,",
        "  if(complete_scope_total_abs_delta = 0, NULL, round(abs_delta / complete_scope_total_abs_delta * 100, 2)) AS contribution_pct",
        "FROM scored",
        f"ORDER BY abs_delta DESC, {dimension_order}",
        f"LIMIT {limit}",
    ]
    sql = "\n".join(sql_lines) + "\n"

    baseline_normalization = {
        "method": (
            "scale_baseline_to_current_window_duration"
            if baseline_value_semantic == "duration_normalized_to_current_window"
            else "none"
        ),
        "current_duration_expression": "dateDiff('second', current_start, current_end)",
        "baseline_duration_expression": " + ".join(
            f"dateDiff('second', baseline_start_{index}, baseline_end_{index})"
            for index in range(1, len(baselines) + 1)
        ),
        "factor_expression": (
            "current_duration_seconds / baseline_average_duration_seconds"
            if baseline_value_semantic == "duration_normalized_to_current_window"
            and baseline_method == "mean_of_baseline_windows"
            else "current_duration_seconds / baseline_total_duration_seconds"
            if baseline_value_semantic == "duration_normalized_to_current_window"
            else "1"
        ),
        "applies_to": ["baseline"] if baseline_value_semantic == "duration_normalized_to_current_window" else [],
    }
    provenance_base = {
        "generator_name": SQL_GENERATOR_NAME,
        "generator_version": SQL_GENERATOR_VERSION,
        "template_id": SQL_TEMPLATE_ID,
        "selected_table": table_name,
        "selected_columns": selected_columns,
        "selected_column_metadata": selected_column_metadata,
        "metadata_origin": metadata_origin,
        "metadata_fingerprint": metadata_hash,
        "metadata_retrieval_identity": metadata_retrieval_identity,
        "metadata_fixture_identity": metadata_fixture_identity,
        "merge_expressions": merge_expressions,
        "metric": metric_name,
        "metric_kind": metric_info["metric_kind"],
        "metric_expression": metric_expression,
        "support_expression": support_expression,
        "metric_semantics_reviewed": True,
        "dimensions": requested_dimensions,
        "grouped_dimensions": requested_dimensions,
        "scope": scope or {},
        "filters": filters or {},
        "applied_scope_filters": applied_scope_filters or {},
        "sql_predicates": sql_predicates,
        "current_window": current,
        "baseline_windows": baselines,
        "baseline_method": baseline_method,
        "baseline_value_semantic": baseline_value_semantic,
        "baseline_normalization": baseline_normalization,
        "limit_stage": "after_denominator",
        "output_limit": limit,
        "source_limit_applied": bool(source_limit_applied),
        "source_limit_stage": source_limit_stage,
    }
    fingerprint_payload = {
        **provenance_base,
        "sql": sql,
        "schema_version": SQL_TEMPLATE_SCHEMA,
    }
    fingerprint = query_fingerprint(fingerprint_payload)
    provenance = {
        **provenance_base,
        "query_fingerprint": fingerprint,
        "trust_state": "assertion_until_direct_mcp_wrapper_result_digest",
    }
    source_limit_before_denominator = bool(source_limit_applied and source_limit_stage == "before_denominator")
    complete_scope_evidence = {
        **provenance_base,
        "evidence_id": "complete-scope-pre-limit-v1",
        "evidence_type": "complete_scope_pre_limit_evidence",
        "applies_to": {"scope": "report"},
        "evidence_source": "trusted_template_generator",
        "query_fingerprint": fingerprint,
        "denominator_expression": f"sum(abs({current_metric_alias} - {baseline_metric_alias})) over ()",
        "computed_over_complete_grouped_scope": True,
        "computed_before_output_limit": True,
        "source_limit_applied_before_denominator": source_limit_before_denominator,
        "trust_state": "assertion_until_direct_mcp_wrapper_result_digest",
    }
    zero_fill_evidence = {
        **provenance_base,
        "evidence_id": "zero-fill-full-scope-join-v1",
        "evidence_type": "zero_fill_evidence",
        "applies_to": {"scope": "report"},
        "evidence_source": "trusted_template_generator",
        "query_fingerprint": fingerprint,
        "period_value_trust": {
            "current": "trusted_full_scope_join",
            "baseline": "trusted_full_scope_join",
        },
        "grouped_scope_complete": True,
        "full_scope_joined_grouped_rowset": True,
        "computed_before_output_limit": True,
        "source_limit_applied_before_zero_fill": source_limit_before_denominator,
        "trust_state": "assertion_until_direct_mcp_wrapper_result_digest",
    }
    return {
        "schema_version": SQL_TEMPLATE_SCHEMA,
        "sql": sql,
        "provenance": provenance,
        "evidence_assertions": [complete_scope_evidence, zero_fill_evidence],
        "summary_validation": summary_validation,
    }


def first_number(row: dict[str, Any], keys: Iterable[str]) -> float | None:
    for key in keys:
        if key not in row:
            continue
        value = to_number(row[key])
        if value is not None:
            return value
    return None


def normalize_period(value: Any) -> str | None:
    if value is None:
        return None
    return ROW_SHAPE_PERIOD_ALIASES.get(str(value).strip().lower())


def extract_metadata_layer(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    metadata: dict[str, Any] = {}
    sidecar = value.get("metadata")
    if isinstance(sidecar, dict):
        for key in REPORT_FIELDS:
            if key in sidecar:
                metadata[key] = sidecar[key]
        for key in TRUST_METADATA_FIELDS:
            if key in sidecar:
                metadata[key] = sidecar[key]
        for key in (
            "caller_metric_kind_assertion",
            "metric_kind",
            "complete_scope_total_abs_delta",
            "scorecard_export_safe",
        ):
            if key in sidecar:
                metadata[key] = sidecar[key]
    for key in REPORT_FIELDS:
        if key in value and key not in {"rows", "columns", "data"}:
            metadata[key] = value[key]
    for key in TRUST_METADATA_FIELDS:
        if key in value and key not in {"rows", "columns", "data"}:
            metadata[key] = value[key]
    for key in (
        "caller_metric_kind_assertion",
        "metric_kind",
        "complete_scope_total_abs_delta",
        "scorecard_export_safe",
    ):
        if key in value:
            metadata[key] = value[key]
    return metadata


def has_row_payload(value: dict[str, Any]) -> bool:
    return isinstance(value.get("rows"), list) or isinstance(value.get("data"), list)


def unwrap_input_doc(input_doc: Any) -> tuple[Any, dict[str, Any]]:
    if isinstance(input_doc, list):
        return input_doc, {}

    current = input_doc
    metadata_stack: list[dict[str, Any]] = []
    seen: set[int] = set()
    while isinstance(current, dict):
        metadata_stack.append(extract_metadata_layer(current))
        if has_row_payload(current):
            break
        next_value = None
        for key in WRAPPER_KEYS:
            nested = current.get(key)
            if isinstance(nested, (dict, list)) and id(nested) not in seen:
                seen.add(id(nested))
                next_value = nested
                break
        if next_value is None:
            break
        current = next_value

    metadata: dict[str, Any] = {}
    for layer in metadata_stack:
        metadata.update(layer)
    return current, metadata


def resolve_value(payload: Any, metadata: dict[str, Any], key: str) -> Any:
    if isinstance(payload, dict) and key in payload:
        return payload[key]
    return metadata.get(key)


def is_scalar_dimension_value(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def normalize_dimension_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def column_names(columns: list[Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for index, column in enumerate(columns):
        if isinstance(column, str):
            name = column.strip()
        elif isinstance(column, dict):
            raw_name = column.get("name")
            if raw_name is None:
                raw_name = column.get("column")
            if not isinstance(raw_name, str):
                raise_invalid(
                    "invalid_mcp_column",
                    "MCP column names must be non-blank strings.",
                    path=f"$.columns[{index}]",
                )
            name = raw_name.strip()
        else:
            raise_invalid(
                "invalid_mcp_column",
                "MCP column names must be non-blank strings.",
                path=f"$.columns[{index}]",
            )
        if not name:
            raise_invalid(
                "blank_mcp_column",
                f"MCP column {index + 1} is blank.",
                path=f"$.columns[{index}]",
            )
        if name in seen:
            raise_invalid(
                "duplicate_mcp_column",
                f"MCP column '{name}' is duplicated.",
                path=f"$.columns[{index}]",
                details={"column": name},
            )
        seen.add(name)
        names.append(name)
    return names


def result_rows(payload: Any) -> tuple[list[dict[str, Any]], str]:
    if isinstance(payload, list):
        if not all(isinstance(row, dict) for row in payload):
            raise_invalid(
                "unmappable_mcp_row",
                "List input must contain row objects.",
                path="$",
            )
        return list(payload), "row_objects"

    if not isinstance(payload, dict):
        raise_invalid(
            "rows_missing",
            "Input must contain aggregate rows or MCP-style columns and rows.",
        )

    rows = payload.get("rows")
    if not isinstance(rows, list):
        rows = payload.get("data")
    if not isinstance(rows, list):
        raise_invalid(
            "rows_missing",
            "Input must contain aggregate rows or MCP-style columns and rows.",
        )
    if not rows:
        return [], "row_objects"
    if all(isinstance(row, dict) for row in rows):
        return list(rows), "row_objects"

    columns = payload.get("columns")
    if not isinstance(columns, list):
        raise_invalid(
            "unmappable_mcp_row",
            "List-style rows require MCP columns for deterministic mapping.",
            path="$.rows",
        )
    names = column_names(columns)
    converted: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, (list, tuple)):
            raise_invalid(
                "unmappable_mcp_row",
                "MCP rows must be lists after columns are declared.",
                path=f"$.rows[{index}]",
            )
        if len(row) != len(names):
            raise_invalid(
                "mcp_row_length_mismatch",
                f"MCP row {index + 1} has {len(row)} values but {len(names)} columns were declared.",
                path=f"$.rows[{index}]",
            )
        converted.append(dict(zip(names, row)))
    return converted, "mcp_rows"


def resolve_requested_metric(
    options: dict[str, Any],
    payload: Any,
    metadata: dict[str, Any],
) -> str | None:
    cli_metric = options.get("metric")
    input_metric = resolve_value(payload, metadata, "metric")
    if cli_metric and input_metric:
        cli_canonical = normalize_metric_name(cli_metric)
        input_canonical = normalize_metric_name(str(input_metric))
        if cli_canonical and input_canonical and cli_canonical != input_canonical:
            raise_invalid(
                "metric_conflict",
                "CLI metric conflicts with the input metric.",
                details={"cli_metric": cli_metric, "input_metric": input_metric},
            )
    selected = cli_metric or input_metric
    if selected is None:
        return None
    entry = metric_entry(str(selected))
    return str(entry["name"])


def infer_metric(rows: list[dict[str, Any]]) -> str:
    candidates: set[str] = set()
    for metric_name in METRIC_ALLOWLIST:
        has_combined = any(
            first_number(row, current_metric_keys(metric_name)) is not None
            and first_number(row, baseline_metric_keys(metric_name)) is not None
            for row in rows
        )
        has_period_split = any(
            normalize_period(row.get("period")) is not None
            and first_number(row, period_metric_keys(metric_name)) is not None
            for row in rows
        )
        if has_combined or has_period_split:
            candidates.add(metric_name)

    if not candidates:
        raise_invalid(
            "metric_input_missing",
            "Specify --metric or include a single unambiguous reviewed metric in the rows.",
        )
    if len(candidates) > 1:
        raise_invalid(
            "ambiguous_metric_input",
            "Input contains multiple reviewed metric candidates; specify --metric.",
            details={"metric_candidates": sorted(candidates)},
        )
    return next(iter(candidates))


def resolve_metric(
    payload: Any,
    metadata: dict[str, Any],
    rows: list[dict[str, Any]],
    options: dict[str, Any],
) -> dict[str, Any]:
    metric_name = resolve_requested_metric(options, payload, metadata) or infer_metric(rows)
    entry = metric_entry(metric_name)
    return {
        "metric": str(entry["name"]),
        "metric_kind": str(entry["metric_kind"]),
    }


def infer_dimensions(rows: list[dict[str, Any]], metric_name: str) -> list[str]:
    if not rows:
        raise_invalid(
            "no_inferable_dimensions",
            "No rows are available for dimension inference.",
        )

    excluded = set(DIMENSION_INFERENCE_EXACT_EXCLUSIONS)
    excluded.update(metric_aliases(metric_name))
    excluded.update(current_metric_keys(metric_name))
    excluded.update(baseline_metric_keys(metric_name))

    inferred: list[str] = []
    for key in rows[0]:
        if key in excluded or key in METADATA_KEYS:
            continue
        if key.startswith(("current_", "baseline_")):
            continue
        if key.endswith(("_current", "_baseline")):
            continue
        if not is_scalar_dimension_value(rows[0].get(key)):
            continue
        inferred.append(key)

    if not inferred:
        raise_invalid(
            "no_inferable_dimensions",
            "Input does not contain deterministic dimension columns.",
        )

    expected = tuple(inferred)
    for index, row in enumerate(rows[1:], start=1):
        row_inferred = []
        for key in row:
            if key in excluded or key in METADATA_KEYS:
                continue
            if key.startswith(("current_", "baseline_")):
                continue
            if key.endswith(("_current", "_baseline")):
                continue
            if not is_scalar_dimension_value(row.get(key)):
                continue
            row_inferred.append(key)
        if tuple(row_inferred) != expected:
            raise_invalid(
                "dimension_inference_ambiguous",
                "Rows do not expose a stable inferred dimension set.",
                path=f"$.rows[{index}]",
            )
    return inferred


def resolve_dimensions(
    payload: Any,
    metadata: dict[str, Any],
    rows: list[dict[str, Any]],
    metric_name: str,
    options: dict[str, Any],
) -> tuple[list[str], bool]:
    cli_dimensions = parse_dimensions(options.get("dimensions"))
    input_dimensions = parse_dimensions(resolve_value(payload, metadata, "dimensions"))
    grouped_dimensions = parse_dimensions(resolve_value(payload, metadata, "grouped_dimensions"))
    if input_dimensions and grouped_dimensions and input_dimensions != grouped_dimensions:
        raise_invalid(
            "dimension_conflict",
            "Input dimensions conflict with grouped_dimensions.",
            details={
                "dimensions": input_dimensions,
                "grouped_dimensions": grouped_dimensions,
            },
        )
    if not input_dimensions:
        input_dimensions = grouped_dimensions
    if cli_dimensions and input_dimensions and cli_dimensions != input_dimensions:
        raise_invalid(
            "dimension_conflict",
            "CLI dimensions conflict with input dimensions.",
            details={
                "cli_dimensions": cli_dimensions,
                "input_dimensions": input_dimensions,
            },
        )
    if cli_dimensions:
        return cli_dimensions, False
    if input_dimensions:
        return input_dimensions, False
    return infer_dimensions(rows, metric_name), True


def validate_baseline_metadata(payload: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    baseline_method = resolve_value(payload, metadata, "baseline_method")
    baseline_value_semantic = resolve_value(payload, metadata, "baseline_value_semantic")
    baseline_windows = resolve_value(payload, metadata, "baseline_windows")

    if baseline_method is not None:
        baseline_method_text = str(baseline_method).strip()
        if baseline_method_text not in BASELINE_METHODS:
            raise_invalid(
                "baseline_method_invalid",
                f"Unsupported baseline_method '{baseline_method}'.",
            )
        baseline_method = baseline_method_text

    if baseline_value_semantic is None:
        baseline_value_semantic = "raw_total_window"
    if baseline_value_semantic is not None:
        semantic_text = str(baseline_value_semantic).strip()
        if semantic_text not in BASELINE_VALUE_SEMANTICS:
            raise_invalid(
                "baseline_value_semantic_invalid",
                f"Unsupported baseline_value_semantic '{baseline_value_semantic}'.",
            )
        baseline_value_semantic = semantic_text

    if baseline_windows is not None and not isinstance(baseline_windows, list):
        raise_invalid(
            "baseline_windows_invalid",
            "baseline_windows must be a list when provided.",
        )
    if isinstance(baseline_windows, list) and len(baseline_windows) > 1 and baseline_method is None:
        raise_invalid(
            "baseline_method_missing",
            "Multiple baseline windows require baseline_method metadata.",
        )

    result: dict[str, Any] = {}
    if baseline_method is not None:
        result["baseline_method"] = baseline_method
    if baseline_value_semantic is not None:
        result["baseline_value_semantic"] = baseline_value_semantic
    if isinstance(baseline_windows, list):
        result["baseline_windows"] = baseline_windows
    return result


def detect_row_shape(row: dict[str, Any], metric_name: str) -> str | None:
    has_combined = (
        first_number(row, current_metric_keys(metric_name)) is not None
        and first_number(row, baseline_metric_keys(metric_name)) is not None
    )
    has_period_split = (
        normalize_period(row.get("period")) is not None
        and first_number(row, period_metric_keys(metric_name)) is not None
    )
    if has_combined and has_period_split:
        return "mixed"
    if has_combined:
        return "combined"
    if has_period_split:
        return "period_split"
    return None


def extract_dimension_values(
    row: dict[str, Any],
    dimensions: list[str],
    *,
    path: str,
) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    for dimension in dimensions:
        if dimension not in row:
            raise_invalid(
                "missing_requested_dimension",
                f"Row is missing requested dimension '{dimension}'.",
                path=path,
                details={"dimension": dimension},
            )
        if not is_scalar_dimension_value(row[dimension]):
            raise_invalid(
                "non_scalar_dimension_value",
                f"Row dimension '{dimension}' must be a scalar value.",
                path=f"{path}.{dimension}",
                details={"dimension": dimension},
            )
        values[dimension] = normalize_dimension_value(row[dimension])
    return values


def entity_key(dimension_values: dict[str, str | None], dimensions: list[str]) -> str:
    return json.dumps(
        [[dimension, dimension_values[dimension]] for dimension in dimensions],
        ensure_ascii=True,
        separators=(",", ":"),
    )


def metric_support_uses_metric_value(metric_kind: str) -> bool:
    return metric_kind == "additive_count"


def explicit_support_value(row: dict[str, Any], period: str) -> float | None:
    if period == "current":
        return first_number(row, CURRENT_SUPPORT_KEYS)
    return first_number(row, BASELINE_SUPPORT_KEYS)


def resolve_support_value(metric_kind: str, explicit_value: float | None, metric_value: float | None) -> float | None:
    if explicit_value is not None:
        return explicit_value
    if metric_support_uses_metric_value(metric_kind):
        return metric_value
    return None


def optional_row_number(row: dict[str, Any], key: str, *, path: str) -> tuple[bool, float | None]:
    if key not in row:
        return False, None
    if row[key] is None:
        return True, None
    value = to_number(row[key])
    if value is None:
        raise_invalid(
            "non_finite_digest_value",
            f"Digest-relevant row field '{key}' must be a finite numeric value or null.",
            path=f"{path}.{key}",
        )
    return True, value


def add_optional_digest_row_fields(
    canonical_row: dict[str, Any],
    source_row: dict[str, Any],
    metric_name: str,
    *,
    path: str,
) -> None:
    optional_keys = (
        f"baseline_raw_{metric_name}",
        "baseline_raw",
        "absolute_delta",
        "abs_delta",
        "pct_change",
        "complete_scope_total_abs_delta",
        "contribution_pct",
        "baseline_normalization_factor",
    )
    for key in optional_keys:
        present, value = optional_row_number(source_row, key, path=path)
        if not present:
            continue
        target_key = "baseline_raw" if key == f"baseline_raw_{metric_name}" else key
        if target_key not in canonical_row:
            canonical_row[target_key] = value


def normalize_combined_rows(
    rows: list[dict[str, Any]],
    metric_name: str,
    metric_kind: str,
    dimensions: list[str],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    canonical_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        path = f"$.rows[{index}]"
        dimension_values = extract_dimension_values(row, dimensions, path=path)
        key = entity_key(dimension_values, dimensions)
        if key in seen:
            raise_invalid(
                "duplicate_entity_key",
                "Duplicate combined-row entity key.",
                path=path,
                details={"entity_key": key},
            )
        current = first_number(row, current_metric_keys(metric_name))
        baseline = first_number(row, baseline_metric_keys(metric_name))
        if current is None or baseline is None:
            raise_invalid(
                "no_usable_metric_values",
                f"Row does not contain comparable current/baseline values for metric '{metric_name}'.",
                path=path,
                details={"metric": metric_name},
            )
        seen.add(key)
        canonical_row = {
            "dimensions": dimension_values,
            "entity_key": key,
            "current": current,
            "baseline": baseline,
            "current_support_raw": resolve_support_value(
                metric_kind,
                explicit_support_value(row, "current"),
                current,
            ),
            "baseline_support_raw": resolve_support_value(
                metric_kind,
                explicit_support_value(row, "baseline"),
                baseline,
            ),
            "baseline_support_normalized": first_number(row, BASELINE_SUPPORT_NORMALIZED_KEYS),
            "input_index": index,
        }
        add_optional_digest_row_fields(canonical_row, row, metric_name, path=path)
        canonical_rows.append(canonical_row)
    return canonical_rows


def normalize_period_split_rows(
    rows: list[dict[str, Any]],
    metric_name: str,
    metric_kind: str,
    dimensions: list[str],
    baseline_metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    seen_period_keys: set[tuple[str, str]] = set()
    baseline_windows = baseline_metadata.get("baseline_windows")
    multiple_baseline_windows = isinstance(baseline_windows, list) and len(baseline_windows) > 1

    for index, row in enumerate(rows):
        path = f"$.rows[{index}]"
        dimension_values = extract_dimension_values(row, dimensions, path=path)
        key = entity_key(dimension_values, dimensions)
        period = normalize_period(row.get("period"))
        if multiple_baseline_windows and period == "baseline" and "baseline_window_label" in row:
            raise_invalid(
                "baseline_windows_not_reduced",
                "Period-split rows must be pre-reduced to one value per entity and period.",
                path=path,
                details={"entity_key": key, "period": period},
            )
        metric_value = first_number(row, period_metric_keys(metric_name))
        if period is None or metric_value is None:
            raise_invalid(
                "no_usable_metric_values",
                f"Row does not contain a usable period-split value for metric '{metric_name}'.",
                path=path,
                details={"metric": metric_name},
            )
        period_key = (key, period)
        if period_key in seen_period_keys:
            code = "baseline_windows_not_reduced" if multiple_baseline_windows and period == "baseline" else "duplicate_entity_period_key"
            raise_invalid(
                code,
                "Period-split rows must be pre-reduced to one value per entity and period.",
                path=path,
                details={"entity_key": key, "period": period},
            )
        seen_period_keys.add(period_key)
        if key not in grouped:
            grouped[key] = {
                "dimensions": dimension_values,
                "entity_key": key,
                "current": None,
                "baseline": None,
                "current_support_raw": None,
                "baseline_support_raw": None,
                "baseline_support_normalized": None,
                "input_index": index,
            }
            order.append(key)
        grouped[key][period] = metric_value
        support_value = resolve_support_value(
            metric_kind,
            first_number(row, PERIOD_SUPPORT_KEYS),
            metric_value,
        )
        grouped[key][f"{period}_support_raw"] = support_value
        if period == "baseline":
            grouped[key]["baseline_support_normalized"] = first_number(
                row,
                BASELINE_SUPPORT_NORMALIZED_KEYS,
            )
        add_optional_digest_row_fields(grouped[key], row, metric_name, path=path)

    return [grouped[key] for key in order]


def heuristic_summary_table_used(table_used: Any) -> bool | None:
    if not isinstance(table_used, str) or not table_used.strip():
        return None
    return table_used not in {"bot_detection", "bot_detection_siem"}


def collect_report_metadata(payload: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    report_metadata: dict[str, Any] = {}
    for key in (
        "analysis_type",
        "comparison_type",
        "granularity",
        "scope",
        "filters",
        "applied_scope_filters",
        "current_window",
        "policy_change",
        "policy_change_window",
        "reviewed_policy",
        "target_effect",
        "table_used",
    ):
        value = resolve_value(payload, metadata, key)
        if value is not None:
            report_metadata[key] = value
    summary_table_used = resolve_value(payload, metadata, "summary_table_used")
    summary_bool = to_bool(summary_table_used)
    if summary_bool is None:
        summary_bool = heuristic_summary_table_used(report_metadata.get("table_used"))
    if summary_bool is not None:
        report_metadata["summary_table_used"] = summary_bool
    source_limit_applied = to_bool(resolve_value(payload, metadata, "source_limit_applied"))
    if source_limit_applied is not None:
        report_metadata["source_limit_applied"] = source_limit_applied
    return report_metadata


def summary_validation_for_report(
    report_metadata: dict[str, Any],
    dimensions: list[str],
) -> dict[str, Any] | None:
    if report_metadata.get("summary_table_used") is not True:
        return None
    table_used = report_metadata.get("table_used")
    if not isinstance(table_used, str) or not table_used.strip():
        return None
    return validate_summary_table_support(
        table_used,
        dimensions,
        scope=report_metadata.get("scope"),
        filters=report_metadata.get("filters"),
        applied_scope_filters=report_metadata.get("applied_scope_filters"),
    )


def collect_input_assertions(payload: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    input_assertions: dict[str, Any] = {}
    caller_metric_kind = resolve_value(payload, metadata, "caller_metric_kind_assertion")
    if caller_metric_kind is None:
        caller_metric_kind = resolve_value(payload, metadata, "metric_kind")
    if caller_metric_kind is not None:
        input_assertions["caller_metric_kind_assertion"] = caller_metric_kind
    for key in (
        "rowset_complete",
        "contribution_basis",
        "complete_scope_total_abs_delta",
        "scorecard_export_safe",
        "output_limit_applied",
        "output_limit",
        "source_limit_applied",
        "evidence_source",
        "period_value_trust",
        "query_fingerprint",
        "result_digest",
        "trusted_context",
        "trusted_evidence",
    ):
        value = resolve_value(payload, metadata, key)
        if value is not None:
            input_assertions[key] = value
    return input_assertions


def normalize_input_rows(input_doc: Any, *, options: Any = None) -> dict[str, Any]:
    opts = normalize_options(options)
    payload, metadata = unwrap_input_doc(input_doc)
    rows, row_source = result_rows(payload)
    if not rows:
        raise_invalid(
            "rows_missing",
            "Input must contain aggregate rows or MCP-style columns and rows.",
        )

    metric_info = resolve_metric(payload, metadata, rows, opts)
    dimensions, dimensions_inferred = resolve_dimensions(payload, metadata, rows, metric_info["metric"], opts)
    baseline_metadata = validate_baseline_metadata(payload, metadata)
    analysis_type = resolve_analysis_type(payload, metadata, opts)

    row_shapes: set[str] = set()
    unusable_rows: list[int] = []
    for index, row in enumerate(rows):
        shape = detect_row_shape(row, metric_info["metric"])
        if shape == "mixed":
            raise_invalid(
                "mixed_row_shapes",
                "Rows cannot mix combined and period-split fields.",
                path=f"$.rows[{index}]",
            )
        if shape is None:
            unusable_rows.append(index)
            continue
        row_shapes.add(shape)

    if not row_shapes:
        raise_invalid(
            "no_usable_metric_values",
            f"Rows do not contain usable values for metric '{metric_info['metric']}'.",
            details={"metric": metric_info["metric"]},
        )
    if len(row_shapes) > 1:
        raise_invalid(
            "mixed_row_shapes",
            "Input cannot mix combined rows and period-split rows.",
        )
    if unusable_rows:
        raise_invalid(
            "no_usable_metric_values",
            "Every row must map to the selected row shape.",
            path=f"$.rows[{unusable_rows[0]}]",
            details={"metric": metric_info["metric"]},
        )

    row_shape = next(iter(row_shapes))
    if row_shape == "combined":
        canonical_rows = normalize_combined_rows(
            rows,
            metric_info["metric"],
            metric_info["metric_kind"],
            dimensions,
        )
    else:
        canonical_rows = normalize_period_split_rows(
            rows,
            metric_info["metric"],
            metric_info["metric_kind"],
            dimensions,
            baseline_metadata,
        )

    limitations: list[str] = []
    if row_source == "mcp_rows" and not any(
        resolve_value(payload, metadata, field) is not None
        for field in ("metric", "dimensions", "baseline_method", "baseline_value_semantic")
    ):
        limitations.append("metadata_poor_input")
    if dimensions_inferred:
        limitations.append("dimensions_inferred")

    report_metadata = collect_report_metadata(payload, metadata)
    report_metadata["analysis_type"] = analysis_type
    summary_validation = summary_validation_for_report(report_metadata, dimensions)
    if summary_validation is not None:
        limitations.extend(summary_validation["limitations"])

    normalized = {
        "metric": metric_info["metric"],
        "metric_kind": metric_info["metric_kind"],
        "analysis_type": analysis_type,
        "dimensions": dimensions,
        "row_shape": row_shape,
        "canonical_rows": canonical_rows,
        "dimensions_inferred": dimensions_inferred,
        "limitations": limitations,
        "report_metadata": report_metadata,
        "trust_metadata": collect_trust_metadata(payload, metadata),
    }
    if summary_validation is not None:
        normalized["summary_validation"] = summary_validation
    normalized.update(baseline_metadata)
    input_assertions = collect_input_assertions(payload, metadata)
    if input_assertions:
        normalized["input_assertions"] = input_assertions
    return normalized


def classify_row(
    row: dict[str, Any],
    *,
    min_count: float,
) -> dict[str, Any]:
    current = row["current"]
    baseline = row["baseline"]
    current_support = row.get("current_support_raw")
    baseline_support = row.get("baseline_support_raw")

    if current is None or baseline is None:
        return {"emit": False, "skip_reason": "period_absence_not_trusted"}

    if current_support is None or baseline_support is None:
        return {
            "emit": True,
            "presence_lifecycle": "not_evaluated",
            "support_change_label": "not_evaluated",
            "candidate_flags": [],
            "confidence_reasons": ["lifecycle_not_evaluated", "lifecycle_support_missing"],
        }

    if current_support <= 0 and baseline_support <= 0:
        return {"emit": False, "skip_reason": "period_absence_not_trusted"}

    if current_support > 0 and baseline_support > 0:
        reasons: list[str] = []
        candidate_flags: list[str] = []
        if current_support < min_count and baseline_support < min_count:
            candidate_flags.append("below_support_threshold")
            reasons.append("sparse_counts")
        elif current_support < min_count:
            candidate_flags.append("sparse_current_support")
            reasons.append("sparse_counts")
        elif baseline_support < min_count:
            candidate_flags.append("sparse_baseline_support")
            reasons.append("sparse_counts")
        else:
            reasons.extend(["current_support_sufficient", "baseline_support_sufficient"])

        if current_support > baseline_support:
            support_change = "support_increase"
        elif current_support < baseline_support:
            support_change = "support_decrease"
        else:
            support_change = "support_unchanged"
        reasons.append(support_change)

        return {
            "emit": True,
            "presence_lifecycle": "existing",
            "support_change_label": support_change,
            "candidate_flags": candidate_flags,
            "confidence_reasons": reasons,
        }

    if baseline_support <= 0 < current_support:
        if current_support < min_count:
            return {
                "emit": True,
                "presence_lifecycle": "not_evaluated",
                "support_change_label": "not_evaluated",
                "candidate_flags": ["sparse_new_candidate"],
                "confidence_reasons": ["lifecycle_not_evaluated", "sparse_counts"],
            }
        return {"emit": False, "skip_reason": "period_absence_not_trusted"}

    if current_support <= 0 < baseline_support:
        if baseline_support < min_count:
            return {
                "emit": True,
                "presence_lifecycle": "not_evaluated",
                "support_change_label": "not_evaluated",
                "candidate_flags": ["sparse_disappeared_candidate"],
                "confidence_reasons": ["lifecycle_not_evaluated", "sparse_counts"],
            }
        return {"emit": False, "skip_reason": "period_absence_not_trusted"}

    return {
        "emit": True,
        "presence_lifecycle": "not_evaluated",
        "support_change_label": "not_evaluated",
        "candidate_flags": [],
        "confidence_reasons": ["lifecycle_not_evaluated"],
    }


def dimension_sort_key(values: dict[str, str | None], dimensions: list[str]) -> tuple[tuple[bool, str], ...]:
    return tuple(
        (values.get(dimension) is None, "" if values.get(dimension) is None else str(values.get(dimension)))
        for dimension in dimensions
    )


def limitation_doc(code: str) -> dict[str, Any]:
    severity, message = LIMITATION_MESSAGES[code]
    return {"code": code, "severity": severity, "message": message}


def build_buckets(movers: list[dict[str, Any]]) -> dict[str, Any]:
    buckets = {
        "basis": "returned_rows",
        "increasing_count": 0,
        "decreasing_count": 0,
        "existing_count": 0,
        "new_count": 0,
        "disappeared_count": 0,
        "absent_count": 0,
        "not_evaluated_count": 0,
        "support_increase_count": 0,
        "support_decrease_count": 0,
        "support_unchanged_count": 0,
        "support_zero_both_count": 0,
        "support_not_evaluated_count": 0,
    }
    for mover in movers:
        if mover["direction"] == "increase":
            buckets["increasing_count"] += 1
        elif mover["direction"] == "decrease":
            buckets["decreasing_count"] += 1

        lifecycle = mover["presence_lifecycle"]
        if lifecycle == "existing":
            buckets["existing_count"] += 1
        elif lifecycle == "new":
            buckets["new_count"] += 1
        elif lifecycle == "disappeared":
            buckets["disappeared_count"] += 1
        elif lifecycle == "not_evaluated":
            buckets["not_evaluated_count"] += 1

        support_change = mover["support_change_label"]
        if support_change == "support_increase":
            buckets["support_increase_count"] += 1
        elif support_change == "support_decrease":
            buckets["support_decrease_count"] += 1
        elif support_change == "support_unchanged":
            buckets["support_unchanged_count"] += 1
        elif support_change == "not_evaluated":
            buckets["support_not_evaluated_count"] += 1
    return buckets


def movement_extreme(
    movers: list[dict[str, Any]],
    *,
    direction_name: str,
) -> dict[str, Any] | None:
    selected = [
        mover
        for mover in movers
        if mover.get("direction") == direction_name
    ]
    if not selected:
        return None
    if direction_name == "increase":
        mover = max(selected, key=lambda item: float(item["absolute_delta"]))
    else:
        mover = min(selected, key=lambda item: float(item["absolute_delta"]))
    return {
        "rank": mover.get("rank"),
        "values": mover.get("values", {}),
        "absolute_delta": mover.get("absolute_delta"),
        "pct_change": mover.get("pct_change"),
        "confidence": mover.get("confidence"),
    }


def displacement_summary(movers: list[dict[str, Any]]) -> dict[str, Any]:
    positive_delta = sum(
        max(float(mover["absolute_delta"]), 0.0)
        for mover in movers
    )
    negative_delta = sum(
        min(float(mover["absolute_delta"]), 0.0)
        for mover in movers
    )
    summary = {
        "basis": "returned_rows",
        "increase_count": sum(1 for mover in movers if mover["direction"] == "increase"),
        "decrease_count": sum(1 for mover in movers if mover["direction"] == "decrease"),
        "total_positive_delta": clean_number(positive_delta),
        "total_negative_delta": clean_number(negative_delta),
        "net_delta": clean_number(positive_delta + negative_delta),
        "largest_increase": movement_extreme(movers, direction_name="increase"),
        "largest_decrease": movement_extreme(movers, direction_name="decrease"),
    }
    return summary


def normalize_attribution(input_doc: Any, trusted_context: Any = None, *, options: Any = None) -> dict[str, Any]:
    normalized = normalize_input_rows(input_doc, options=options)
    opts = normalize_options(options)
    analysis_type = normalized["analysis_type"]

    min_count_value = opts.get("min_count")
    if min_count_value is None:
        min_count_value = 100.0
    min_count = float(min_count_value)

    limit_value = opts.get("limit")
    limit = int(limit_value) if limit_value is not None else 0

    digest_payload = digest_payload_v1_from_normalized(normalized, options=opts)
    recomputed_digest = sha256_payload_digest(digest_payload)
    trust_validation = validate_trusted_context(
        trusted_context,
        normalized,
        digest_payload,
        recomputed_digest,
    )

    report_reasons = {"standalone_confidence_cap"}
    if analysis_type == "policy_displacement":
        report_reasons.add("policy_displacement_review")
    report_reasons.update(trust_validation["reasons"])
    if trusted_context is not None:
        report_reasons.add("trusted_context_reserved_for_future_tasks")

    report_reasons.update(normalized["limitations"])
    limitation_codes = {"aggregate_rows_only", "no_causal_claim", "contribution_withheld"}
    limitation_codes.update(
        reason for reason in trust_validation["reasons"] if reason in LIMITATION_MESSAGES
    )
    if normalized["metric_kind"] != "additive_count":
        report_reasons.add("non_additive_metric_contribution_withheld")
    if normalized.get("input_assertions"):
        report_reasons.add("caller_assertion_not_trusted")
        limitation_codes.add("caller_assertion_not_trusted")

    summary_table_used = normalized["report_metadata"].get("summary_table_used")
    if summary_table_used is True:
        report_reasons.add("summary_table_used")
    elif summary_table_used is False:
        report_reasons.add("raw_table_fallback")
    summary_validation = normalized.get("summary_validation")
    if summary_validation and summary_validation["supported"]:
        report_reasons.add("summary_dimension_set_supported")

    skipped_period_absence: list[dict[str, Any]] = []
    lifecycle_support_missing_values: list[dict[str, Any]] = []
    movers: list[dict[str, Any]] = []
    saw_sparse = False

    for canonical_row in normalized["canonical_rows"]:
        classification = classify_row(canonical_row, min_count=min_count)
        if not classification["emit"]:
            if classification["skip_reason"] == "period_absence_not_trusted":
                skipped_period_absence.append(dict(canonical_row["dimensions"]))
            continue

        current = float(canonical_row["current"])
        baseline = float(canonical_row["baseline"])
        delta = current - baseline
        pct = pct_change(current, baseline)
        pct_guarded = baseline < 1.0
        row_reasons = set(classification["confidence_reasons"])
        candidate_flags = list(classification["candidate_flags"])

        if pct_guarded:
            row_reasons.add("pct_change_guarded")
            if baseline == 0:
                row_reasons.add("zero_baseline_guard")
            else:
                row_reasons.add("subunit_baseline_guard")
        if candidate_flags or "sparse_counts" in row_reasons:
            saw_sparse = True
            report_reasons.add("sparse_counts")
        if "lifecycle_support_missing" in row_reasons:
            lifecycle_support_missing_values.append(dict(canonical_row["dimensions"]))
            limitation_codes.add("lifecycle_support_missing")
            report_reasons.add("lifecycle_support_missing")

        mover = {
            "values": dict(canonical_row["dimensions"]),
            "current": clean_number(current),
            "baseline": clean_number(baseline),
            "absolute_delta": clean_number(delta),
            "pct_change": clean_number(pct),
            "pct_change_guarded": pct_guarded,
            "direction": direction(delta),
            "presence_lifecycle": classification["presence_lifecycle"],
            "support_change_label": classification["support_change_label"],
            "candidate_flags": candidate_flags,
            "confidence": "low" if row_reasons & {"sparse_counts", "lifecycle_support_missing"} else "medium",
            "confidence_reasons": sorted(row_reasons),
        }
        if canonical_row.get("current_support_raw") is not None:
            mover["current_support_raw"] = clean_number(canonical_row["current_support_raw"])
        if canonical_row.get("baseline_support_raw") is not None:
            mover["baseline_support_raw"] = clean_number(canonical_row["baseline_support_raw"])
        if canonical_row.get("baseline_support_normalized") is not None:
            mover["baseline_support_normalized"] = clean_number(canonical_row["baseline_support_normalized"])
        movers.append(mover)

    if skipped_period_absence:
        report_reasons.add("period_absence_not_trusted")
        limitation_codes.add("period_absence_not_trusted")

    if not movers:
        raise_invalid(
            "no_usable_metric_values",
            f"Rows do not contain comparable current/baseline values for metric '{normalized['metric']}'.",
            details={"metric": normalized["metric"]},
        )

    ranked_movers = sorted(
        movers,
        key=lambda mover: (
            -abs(float(mover["absolute_delta"])),
            dimension_sort_key(mover["values"], normalized["dimensions"]),
        ),
    )
    for rank, mover in enumerate(ranked_movers, start=1):
        mover["rank"] = rank

    output_limit_applied = limit > 0 and len(ranked_movers) > limit
    returned_movers = ranked_movers[:limit] if limit > 0 else ranked_movers
    total_current = sum(float(mover["current"]) for mover in returned_movers)
    total_baseline = sum(float(mover["baseline"]) for mover in returned_movers)
    total_delta = total_current - total_baseline
    total_abs_delta = sum(abs(float(mover["absolute_delta"])) for mover in returned_movers)

    if (
        saw_sparse
        or "metadata_poor_input" in report_reasons
        or "raw_table_fallback" in report_reasons
        or "lifecycle_support_missing" in report_reasons
        or "unsupported_summary_dimension_set" in report_reasons
        or "unsupported_summary_filter" in report_reasons
    ):
        confidence = "low"
    else:
        confidence = "medium"

    not_evaluated_components: list[dict[str, Any]] = [
        {
            "name": "contribution_pct",
            "reason": "complete_scope_not_proven",
            "required_metadata": CONTRIBUTION_REQUIRED_METADATA,
        }
    ]
    if skipped_period_absence:
        not_evaluated_components.append(
            {
                "name": "presence_lifecycle",
                "reason": "period_absence_not_trusted",
                "skipped_count": len(skipped_period_absence),
                "sample_entity_values": skipped_period_absence[:SAMPLE_ENTITY_VALUES_LIMIT],
                "required_metadata": ZERO_FILL_REQUIRED_METADATA,
            }
        )
    if lifecycle_support_missing_values:
        not_evaluated_components.append(
            {
                "name": "presence_lifecycle",
                "reason": "lifecycle_support_missing",
                "affected_count": len(lifecycle_support_missing_values),
                "sample_entity_values": lifecycle_support_missing_values[:SAMPLE_ENTITY_VALUES_LIMIT],
            }
        )
    if summary_validation:
        if summary_validation["unsupported_grouped_dimensions"]:
            not_evaluated_components.append(
                {
                    "name": "summary_grouped_dimensions",
                    "reason": "unsupported_summary_dimension_set",
                    "selected_table": summary_validation["selected_table"],
                    "unsupported_columns": summary_validation["unsupported_grouped_dimensions"],
                    "retained_dimensions": summary_validation["retained_dimensions"],
                }
            )
        if summary_validation["unsupported_filter_columns"]:
            not_evaluated_components.append(
                {
                    "name": "summary_scope_filters",
                    "reason": "unsupported_summary_filter",
                    "selected_table": summary_validation["selected_table"],
                    "unsupported_columns": summary_validation["unsupported_filter_columns"],
                    "retained_dimensions": summary_validation["retained_dimensions"],
                }
            )

    method = (
        "policy_displacement_attribution"
        if analysis_type == "policy_displacement"
        else "aggregate_delta_attribution"
    )
    interpretation_constraints = list(INTERPRETATION_CONSTRAINTS)
    if analysis_type == "policy_displacement":
        interpretation_constraints.extend(
            [
                "policy_displacement_review",
                "requires_external_policy_change_evidence",
            ]
        )

    result = {
        "schema_version": ATTRIBUTION_SCHEMA,
        "method": method,
        "analysis_type": analysis_type,
        "metric": normalized["metric"],
        "metric_kind": normalized["metric_kind"],
        "dimensions": normalized["dimensions"],
        "row_shape": normalized["row_shape"],
        "rowset_complete": False,
        "source_limit_applied": normalized["report_metadata"].get("source_limit_applied", False),
        "output_limit_applied": output_limit_applied,
        "contribution_basis": "none",
        "totals_basis": "returned_rows",
        "total_current": clean_number(total_current),
        "total_baseline": clean_number(total_baseline),
        "total_delta": clean_number(total_delta),
        "total_abs_delta": clean_number(total_abs_delta),
        "movers": returned_movers,
        "returned_rows": len(returned_movers),
        "buckets": build_buckets(returned_movers),
        "not_evaluated_components": not_evaluated_components,
        "limitations": [limitation_doc(code) for code in sorted(limitation_codes | set(normalized["limitations"]))],
        "confidence": confidence,
        "confidence_reasons": sorted(report_reasons),
        "interpretation_constraints": interpretation_constraints,
    }
    if analysis_type == "policy_displacement":
        result["displacement_summary"] = displacement_summary(returned_movers)

    if limit > 0:
        result["output_limit"] = limit
    for key in (
        "analysis_type",
        "comparison_type",
        "granularity",
        "scope",
        "filters",
        "applied_scope_filters",
        "current_window",
        "policy_change",
        "policy_change_window",
        "reviewed_policy",
        "target_effect",
        "table_used",
        "summary_table_used",
    ):
        if key in normalized["report_metadata"]:
            result[key] = normalized["report_metadata"][key]
    if summary_validation is not None:
        result["summary_validation"] = summary_validation
    if "baseline_method" in normalized:
        result["baseline_method"] = normalized["baseline_method"]
    if "baseline_value_semantic" in normalized:
        result["baseline_value_semantic"] = normalized["baseline_value_semantic"]
    if "baseline_windows" in normalized:
        result["baseline_windows"] = normalized["baseline_windows"]
    if normalized.get("input_assertions"):
        result["input_assertions"] = normalized["input_assertions"]
    if trusted_context is not None:
        result["trusted_context_validation"] = {
            "trusted": trust_validation["trusted"],
            "valid": trust_validation["valid"],
            "result_digest": trust_validation["result_digest"],
            "evidence_types": trust_validation["evidence_types"],
            "reasons": sorted(trust_validation["reasons"]),
        }
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        input_doc = json.loads(read_input(args))
    except json.JSONDecodeError as exc:
        print(
            json.dumps(
                invalid_input_doc("malformed_json", f"Input is not valid JSON: {exc.msg}.")
            ),
            indent=2,
            sort_keys=True,
        )
        return 2
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        result = normalize_attribution(
            input_doc,
            trusted_context=None,
            options={
                "metric": args.metric,
                "dimensions": args.dimensions,
                "analysis": args.analysis,
                "min_count": args.min_count,
                "limit": args.limit,
                "output": args.output,
            },
        )
    except InvalidInputError as exc:
        print(json.dumps(exc.document, indent=2, sort_keys=True))
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
