#!/usr/bin/env python3
"""Render Bot Insights artifacts as Markdown or self-contained HTML.

This script is a deterministic view layer over existing Bot Insights artifacts.
It does not query Hydrolix, recompute scores, or infer metrics beyond the
fields already present in the input JSON.
"""

from __future__ import annotations

import argparse
import copy
import html
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WRAPPER_SCHEMA = "bot_report_input.v1"
POSTURE_SCHEMA = "bot_posture_movement.v1"
MOVER_SCHEMA = "bot_mover_attribution.v1"
CONTROL_SCHEMA = "bot_control_review.v1"
SCORECARD_SCHEMA = "bot_entity_scorecard.v1"
INDEX_SCHEMA = "bot_scorecard_index.v1"
SCORECARD_PACKET_SCHEMA = "bot_scorecard_artifacts.v1"
TIMESERIES_SCHEMA = "bot_timeseries.v1"
CONTROL_EXPECTED_BASES = {"before_window", "explicit_target", "external_model"}

CACHE_ORIGIN_IMPACT_SCHEMA = "cache_origin_impact_report.v1"

SUPPORTED_SCHEMAS = {
    POSTURE_SCHEMA,
    MOVER_SCHEMA,
    CONTROL_SCHEMA,
    SCORECARD_SCHEMA,
    INDEX_SCHEMA,
    SCORECARD_PACKET_SCHEMA,
    TIMESERIES_SCHEMA,
    CACHE_ORIGIN_IMPACT_SCHEMA,
}
KNOWN_UNSUPPORTED_SCHEMAS: set[str] = set()
REPORT_TYPES = {
    "executive_posture",
    "soc_triage",
    "control_review",
    "scorecard_brief",
    "crawler_governance",
    "edge_ops_impact",
}
RESERVED_CHILD_ID = re.compile(r"(#index|#scorecard-\d+)$")

CRAWLER_FEATURES = {
    "rate_429_delta_high",
    "rate_5xx_delta_high",
    "good_bot_429_present",
    "good_bot_error_rate_high",
    "policy_surface_failure_present",
    "ai_crawler_growth_high",
}
GENERIC_CRAWLER_RATE_FEATURES = {"rate_429_delta_high", "rate_5xx_delta_high"}
EDGE_OPS_FEATURES = {
    "cache_miss_rate_high",
    "cache_miss_delta_high",
    "querystring_diversity_high",
    "querystring_diversity_with_high_miss_rate",
    "origin_p95_delta_high",
    "origin_cost_contribution_high",
}
EDGE_OPS_DOMAINS = {"cache_busting", "origin_impact"}


class ReportError(ValueError):
    """Input or rendering error that should produce a CLI failure."""


class ReportContext:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.artifact_id_explicit: dict[str, bool] = {}
        self.generated_child_parent: dict[str, str] = {}

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render Bot Insights artifacts as Markdown or self-contained HTML."
    )
    parser.add_argument(
        "text",
        nargs="*",
        help="Artifact JSON. If omitted, stdin is read.",
    )
    parser.add_argument(
        "-f",
        "--file",
        type=Path,
        help="Read artifact JSON from a file instead of positional arguments/stdin.",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "html"),
        default="markdown",
        help="Output format.",
    )
    parser.add_argument(
        "--report-type",
        choices=sorted(REPORT_TYPES),
        help="Report type to render.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the report to this path instead of stdout.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Display row/card limit. Does not affect artifact validation.",
    )
    parser.add_argument(
        "--allow-unknown",
        action="store_true",
        help="Skip unknown artifact schemas instead of failing.",
    )
    parser.add_argument(
        "--title",
        help="Presentation title override.",
    )
    return parser.parse_args()


def read_input(args: argparse.Namespace) -> str:
    if args.file:
        return args.file.read_text(encoding="utf-8")
    if args.text:
        return " ".join(args.text)
    return sys.stdin.read()


def stringify(value: Any) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ": "))


METRIC_LABELS = {
    "ai_requests": "AI requests",
    "bot_like_requests": "Bot-like requests",
    "cache_misses": "Cache misses",
    "error_5xx_requests": "5xx errors",
    "rate_limited_requests": "429 rate-limited requests",
    "requests": "Total requests",
    "avg_bot_score": "Average bot score",
    "siem_auth_fail_requests": "SIEM auth failures",
    "siem_blocked_requests": "SIEM blocked requests",
    "unique_client_ips": "Unique client IPs",
}


def human_metric_name(value: Any) -> str:
    text = stringify(value)
    return METRIC_LABELS.get(text, text)


def display_label(value: Any) -> str:
    acronym_tokens = {"ai", "api", "asn", "cdn", "ip", "seo", "siem", "url"}
    token_labels = {"querystring": "Query String"}
    words: list[str] = []
    for token in stringify(value).replace("_", " ").split():
        lower = token.lower()
        if lower in token_labels:
            words.append(token_labels[lower])
        elif lower in acronym_tokens:
            words.append(lower.upper())
        elif token and token[0].isdigit():
            words.append(token)
        else:
            words.append(token[:1].upper() + token[1:].lower())
    return " ".join(words)


def rule_label_parts(value: Any) -> tuple[str, str]:
    text = stringify(value)
    known = {
        "new_entity": ("Entity", "New"),
        "volume_delta_high": ("Request Volume", "High Increase"),
        "contribution_to_total_delta_high": ("Contribution To Total", "High Delta"),
        "bot_share_delta_high": ("Bot Share", "High Increase"),
        "cache_miss_rate_high": ("Cache Miss Rate", "High"),
        "cache_miss_delta_high": ("Cache Miss Rate", "High Increase"),
        "origin_p95_delta_high": ("Origin P95", "High Increase"),
        "origin_cost_contribution_high": ("Origin Cost Contribution", "High"),
        "querystring_diversity_high": ("Query String Diversity", "High"),
        "querystring_diversity_with_high_miss_rate": (
            "Query String Diversity",
            "With High Miss Rate",
        ),
        "rate_429_delta_high": ("429 Rate", "High Increase"),
        "rate_5xx_delta_high": ("5xx Rate", "High Increase"),
        "good_bot_429_present": ("Good Bot 429 Responses", "Present"),
        "good_bot_error_rate_high": ("Good Bot Error Rate", "High"),
        "policy_surface_failure_present": ("Policy Surface Failures", "Present"),
        "ai_crawler_growth_high": ("AI Crawler Growth", "High"),
        "good_bot_policy_collateral_present": ("Good Bot Policy Collateral", "Present"),
        "policy_collateral_error_rate_high": ("Policy Collateral Error Rate", "High"),
        "displacement_delta_high": ("Displacement", "High Increase"),
        "siem_blocked_present": ("SIEM Blocked Requests", "Present"),
        "siem_auth_fail_present": ("SIEM Auth Failures", "Present"),
        "bad_bot_share_high": ("Bad Bot Share", "High"),
    }
    return known.get(text, (display_label(text), ""))


def human_number(value: Any, *, percent: bool = False) -> str:
    number = to_float(value)
    if number is None:
        return stringify(value)
    abs_number = abs(number)
    if percent:
        return f"{number:+.1f}%"
    if abs_number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if abs_number >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if abs_number >= 1_000:
        return f"{number / 1_000:.2f}K"
    if float(number).is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"


def human_delta(value: Any) -> str:
    number = to_float(value)
    if number is None:
        return stringify(value)
    sign = "+" if number > 0 else ""
    return sign + human_number(number)


def human_window_range(window: Any) -> str:
    if not isinstance(window, dict):
        return stringify(window)
    start = human_timestamp(window.get("start") or "unknown")
    end = human_timestamp(window.get("end") or "unknown")
    return f"{start} to {end}"


def compact_window_range(window: Any) -> str:
    if not isinstance(window, dict):
        return stringify(window)
    start = parse_utc_timestamp(window.get("start"))
    end = parse_utc_timestamp(window.get("end"))
    if start is None or end is None:
        return human_window_range(window)
    start_date = start.strftime("%b %-d, %Y")
    start_time = start.strftime("%H:%M")
    end_time = end.strftime("%H:%M")
    if end.date() == start.date():
        return f"{start_date}, {start_time}-{end_time} UTC"
    if (end - start).total_seconds() == 86400 and end_time == "00:00":
        return f"{start_date}, {start_time}-24:00 UTC"
    end_date = end.strftime("%b %-d, %Y")
    return f"{start_date} {start_time} - {end_date} {end_time} UTC"


def human_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        return stringify(value)
    match = re.fullmatch(
        r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2})?Z",
        value,
    )
    if not match:
        return value
    year, month, day, hour, minute = match.groups()
    return f"{year}-{month}-{day} {hour}:{minute} UTC"


def parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def human_windows(artifact: dict[str, Any]) -> str:
    current = artifact.get("current_window")
    baselines = artifact.get("baseline_windows")
    parts: list[str] = []
    if current:
        parts.append(f"current {human_window_range(current)}")
    if isinstance(baselines, list) and baselines:
        parts.append(f"baseline {human_window_range(baselines[0])}")
    return "; ".join(parts) if parts else "unavailable"


def metric_by_name(metrics: list[Any], name: str) -> dict[str, Any] | None:
    for metric in metrics:
        if isinstance(metric, dict) and metric.get("name") == name:
            return metric
    return None


def metric_sentence(metric: dict[str, Any]) -> str:
    label = human_metric_name(metric.get("name"))
    direction_text = {
        "increase": "increased",
        "decrease": "decreased",
        "flat": "was flat",
        "no_change": "did not change",
    }.get(stringify(metric.get("direction")), "changed")
    return (
        f"{label} {direction_text} by {human_delta(metric.get('absolute_delta'))} "
        f"({human_number(metric.get('pct_change'), percent=True)}), from "
        f"{human_number(metric.get('baseline'))} baseline to "
        f"{human_number(metric.get('current'))} current."
    )


def executive_summary_lines(metrics: list[Any]) -> list[str]:
    usable = [metric for metric in metrics if isinstance(metric, dict)]
    if not usable:
        return [
            "No posture metrics were available in the artifact; review the evidence limits before drawing conclusions.",
            "This is a movement report, not a root-cause analysis.",
        ]

    lines: list[str] = []
    total = metric_by_name(usable, "requests")
    if total:
        lines.append(metric_sentence(total))
    else:
        lines.append(
            "The artifact does not include total request volume, so the summary is limited to supplied metric deltas."
        )

    ranked = sorted(
        (
            metric
            for metric in usable
            if to_float(metric.get("pct_change")) is not None
            and metric.get("name") != "requests"
        ),
        key=lambda metric: abs(to_float(metric.get("pct_change")) or 0.0),
        reverse=True,
    )
    if ranked:
        leaders = ranked[:3]
        fragments = [
            f"{human_metric_name(metric.get('name'))} {human_number(metric.get('pct_change'), percent=True)}"
            for metric in leaders
        ]
        lines.append("Largest relative movements: " + ", ".join(fragments) + ".")

    review_metrics = [
        metric
        for name in ("cache_misses", "rate_limited_requests", "error_5xx_requests")
        if (metric := metric_by_name(usable, name)) is not None
    ]
    if review_metrics:
        fragments = [
            f"{human_metric_name(metric.get('name'))} {human_delta(metric.get('absolute_delta'))}"
            for metric in review_metrics
        ]
        lines.append("Operational signals to review: " + ", ".join(fragments) + ".")

    lines.append(
        "Treat these changes as evidence of movement only; this report does not identify root cause or malicious intent by itself."
    )
    return lines


def to_float(value: Any) -> float | None:
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


def clean_display(value: float) -> int | float:
    if value.is_integer():
        return int(value)
    return round(value, 6)


_MD_BACKSLASH_CHARS = "`*_{}[]()#+-.!"


def md_escape(value: Any) -> str:
    text = stringify(value)
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("\\", "\\\\")
    text = text.replace("|", "\\|")
    out_chars: list[str] = []
    for ch in text:
        if ch in _MD_BACKSLASH_CHARS:
            out_chars.append("\\" + ch)
        else:
            out_chars.append(ch)
    return "".join(out_chars)


def _demd(text: str) -> str:
    return re.sub(r"\\([\\`*_{}\[\]()#+\-.!|])", r"\1", text)


def _is_escaped_marker(text: str, index: int) -> bool:
    slash_count = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        slash_count += 1
        cursor -= 1
    return slash_count % 2 == 1


def _find_unescaped(text: str, marker: str, start: int = 0) -> int:
    cursor = start
    while True:
        index = text.find(marker, cursor)
        if index == -1:
            return -1
        if not _is_escaped_marker(text, index):
            return index
        cursor = index + 1


def h_escape(value: Any) -> str:
    return html.escape(html.unescape(stringify(value)), quote=True)


def slug_title(report_type: str) -> str:
    return report_type.replace("_", " ").title()


def json_fingerprint(value: Any) -> str:
    sanitized = copy.deepcopy(value)
    if isinstance(sanitized, dict):
        sanitized.pop("artifact_id", None)
    return json.dumps(sanitized, sort_keys=True, separators=(",", ":"))


def duplicate_body_fingerprint(artifact: dict[str, Any]) -> str:
    sanitized = copy.deepcopy(artifact)
    for key in ("artifact_id", "parent_artifact_id", "parent_json_pointer"):
        sanitized.pop(key, None)
    return json.dumps(sanitized, sort_keys=True, separators=(",", ":"))


def reserved_artifact_id(artifact_id: str) -> bool:
    return RESERVED_CHILD_ID.search(artifact_id) is not None


def schema_of(artifact: Any) -> str:
    if isinstance(artifact, dict):
        return str(artifact.get("schema_version", ""))
    return ""


def validate_artifact_schema(
    artifact: Any, allow_unknown: bool, ctx: ReportContext
) -> bool:
    if not isinstance(artifact, dict):
        raise ReportError("Artifact entries must be JSON objects.")
    schema = schema_of(artifact)
    if not schema:
        raise ReportError("Artifact object is missing schema_version.")
    if schema in KNOWN_UNSUPPORTED_SCHEMAS:
        raise ReportError(
            f"{schema} is a known future schema but is unsupported by the MVP renderer."
        )
    if schema in SUPPORTED_SCHEMAS:
        return True
    if allow_unknown:
        ctx.warn(f"Skipped unknown artifact schema {schema}.")
        return False
    raise ReportError(f"Unknown artifact schema {schema}.")


def artifact_with_id(
    artifact: dict[str, Any],
    artifact_id: str,
    *,
    parent_id: str | None = None,
    parent_pointer: str | None = None,
) -> dict[str, Any]:
    copied = copy.deepcopy(artifact)
    copied["artifact_id"] = artifact_id
    if parent_id is not None:
        copied["parent_artifact_id"] = parent_id
    if parent_pointer is not None:
        copied["parent_json_pointer"] = parent_pointer
    return copied


def normalize_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    allow_unknown: bool,
    ctx: ReportContext,
) -> list[dict[str, Any]]:
    all_ids: set[str] = set()
    explicit_input_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []

    def append_unique(
        artifact: dict[str, Any],
        *,
        explicit_id: bool,
        generated_parent_id: str | None = None,
    ) -> None:
        artifact_id = str(artifact["artifact_id"])
        if artifact_id in all_ids:
            raise ReportError(f"Duplicate normalized artifact_id {artifact_id}.")
        all_ids.add(artifact_id)
        ctx.artifact_id_explicit[artifact_id] = explicit_id
        if generated_parent_id is not None:
            ctx.generated_child_parent[artifact_id] = generated_parent_id
        normalized.append(artifact)

    for index, raw in enumerate(artifacts, start=1):
        if not validate_artifact_schema(raw, allow_unknown, ctx):
            continue
        had_explicit = "artifact_id" in raw and raw.get("artifact_id") is not None
        if had_explicit and (
            not isinstance(raw["artifact_id"], str) or not raw["artifact_id"].strip()
        ):
            raise ReportError("Explicit artifact_id must be a non-empty string.")
        artifact_id = raw["artifact_id"] if had_explicit else f"artifact-{index}"
        if reserved_artifact_id(artifact_id):
            raise ReportError(
                f"Artifact ID {artifact_id} uses a reserved generated child suffix."
            )
        if had_explicit:
            if artifact_id in explicit_input_ids:
                raise ReportError(f"Duplicate artifact_id {artifact_id}.")
            explicit_input_ids.add(artifact_id)

        parent = artifact_with_id(raw, artifact_id)
        append_unique(parent, explicit_id=had_explicit)

        if schema_of(raw) == SCORECARD_PACKET_SCHEMA:
            packet_index = raw.get("index")
            if (
                isinstance(packet_index, dict)
                and schema_of(packet_index) == INDEX_SCHEMA
            ):
                child = copy.deepcopy(packet_index)
                append_unique(
                    artifact_with_id(
                        child,
                        f"{artifact_id}#index",
                        parent_id=artifact_id,
                        parent_pointer="/index",
                    ),
                    explicit_id=False,
                    generated_parent_id=artifact_id,
                )
            scorecards = raw.get("scorecards")
            if isinstance(scorecards, list):
                for child_index, scorecard in enumerate(scorecards, start=1):
                    if (
                        not isinstance(scorecard, dict)
                        or schema_of(scorecard) != SCORECARD_SCHEMA
                    ):
                        continue
                    child = copy.deepcopy(scorecard)
                    append_unique(
                        artifact_with_id(
                            child,
                            f"{artifact_id}#scorecard-{child_index}",
                            parent_id=artifact_id,
                            parent_pointer=f"/scorecards/{child_index - 1}",
                        ),
                        explicit_id=False,
                        generated_parent_id=artifact_id,
                    )
    return normalized


def load_report_input(
    value: Any,
    args: argparse.Namespace,
    ctx: ReportContext,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    str | None,
    str | None,
    int | None,
    str | None,
    str | None,
]:
    wrapper_report_type: str | None = None
    wrapper_title: str | None = None
    wrapper_limit: int | None = None
    scope_label: str | None = None
    notes: list[dict[str, Any]] = []
    raw_mode: str | None = None

    if isinstance(value, dict) and value.get("schema_version") == WRAPPER_SCHEMA:
        wrapper_report_type = value.get("report_type")
        if wrapper_report_type is not None and not isinstance(wrapper_report_type, str):
            raise ReportError("Wrapper report_type must be a string.")
        if wrapper_report_type is not None and wrapper_report_type not in REPORT_TYPES:
            raise ReportError(f"Unsupported wrapper report_type {wrapper_report_type}.")
        wrapper_title = value.get("title")
        if wrapper_title is not None and not isinstance(wrapper_title, str):
            raise ReportError("Wrapper title must be a string.")
        wrapper_limit = value.get("limit")
        if wrapper_limit is not None and (
            not isinstance(wrapper_limit, int)
            or isinstance(wrapper_limit, bool)
            or wrapper_limit <= 0
        ):
            raise ReportError("Wrapper limit must be a positive integer.")
        scope_label = value.get("scope_label")
        if scope_label is not None and (
            not isinstance(scope_label, str) or not scope_label.strip()
        ):
            raise ReportError("Wrapper scope_label must be a non-empty string.")
        raw_notes = value.get("analyst_notes", [])
        if raw_notes is None:
            raw_notes = []
        if not isinstance(raw_notes, list) or not all(
            isinstance(note, dict) for note in raw_notes
        ):
            raise ReportError("Wrapper analyst_notes must be an array of objects.")
        notes = raw_notes
        raw_artifacts = value.get("artifacts")
        if not isinstance(raw_artifacts, list) or not raw_artifacts:
            raise ReportError("Wrapper artifacts must be a non-empty array.")
    elif isinstance(value, dict) and "schema_version" in value:
        raw_mode = "single"
        raw_artifacts = [value]
    elif isinstance(value, list):
        if not value:
            raise ReportError("Raw artifact array input must be non-empty.")
        raw_mode = "array"
        raw_artifacts = value
        if args.report_type is None:
            raise ReportError("Raw artifact array input requires --report-type.")
    elif isinstance(value, dict):
        raise ReportError("Raw artifact object input is missing schema_version.")
    else:
        raise ReportError(
            "Input must be a known artifact object, a non-empty artifact array, or a bot_report_input.v1 wrapper."
        )

    if not all(isinstance(artifact, dict) for artifact in raw_artifacts):
        raise ReportError("All artifacts must be JSON objects.")

    normalized = normalize_artifacts(
        raw_artifacts,
        allow_unknown=bool(args.allow_unknown),
        ctx=ctx,
    )
    if not normalized:
        raise ReportError("No supported artifacts were available after normalization.")
    return (
        normalized,
        notes,
        wrapper_report_type,
        wrapper_title,
        wrapper_limit,
        scope_label,
        raw_mode,
    )


def infer_report_type(
    artifacts: list[dict[str, Any]], raw_hint: str | None
) -> str | None:
    if raw_hint != "single":
        return None
    raw_schema = schema_of(artifacts[0]) if artifacts else ""
    mapping = {
        POSTURE_SCHEMA: "executive_posture",
        CONTROL_SCHEMA: "control_review",
        INDEX_SCHEMA: "soc_triage",
    }
    return mapping.get(raw_schema)


def resolve_options(
    artifacts: list[dict[str, Any]],
    *,
    wrapper_report_type: str | None,
    wrapper_title: str | None,
    wrapper_limit: int | None,
    scope_label: str | None,
    raw_mode: str | None,
    args: argparse.Namespace,
    ctx: ReportContext,
) -> tuple[str, str, int, str | None]:
    cli_report_type = args.report_type
    if (
        wrapper_report_type
        and cli_report_type
        and wrapper_report_type != cli_report_type
    ):
        raise ReportError(
            f"Wrapper report_type {wrapper_report_type} conflicts with CLI --report-type {cli_report_type}."
        )
    report_type = wrapper_report_type or cli_report_type
    if report_type is None:
        report_type = infer_report_type(artifacts, raw_mode)
    if report_type is None:
        raise ReportError(
            "Missing or ambiguous report intent; supply --report-type or wrapper report_type."
        )

    if args.title is not None and not isinstance(args.title, str):
        raise ReportError("--title must be a string.")
    if (
        args.title is not None
        and wrapper_title is not None
        and args.title != wrapper_title
    ):
        ctx.warn("CLI --title overrides wrapper title.")
    title = (
        args.title
        or wrapper_title
        or generated_title(report_type, artifacts, scope_label)
    )

    if args.limit is not None:
        if (
            not isinstance(args.limit, int)
            or isinstance(args.limit, bool)
            or args.limit <= 0
        ):
            raise ReportError("--limit must be a positive integer.")
    if (
        args.limit is not None
        and wrapper_limit is not None
        and args.limit != wrapper_limit
    ):
        ctx.warn("CLI --limit overrides wrapper limit.")
    limit = args.limit or wrapper_limit or default_limit(report_type)
    return report_type, title, limit, scope_label


def default_limit(report_type: str) -> int:
    if report_type == "scorecard_brief":
        return 20
    return 10


def generated_title(
    report_type: str, artifacts: list[dict[str, Any]], scope_label: str | None
) -> str:
    scope = scope_label
    if not scope:
        for artifact in artifacts:
            scope_value = artifact.get("scope")
            if isinstance(scope_value, dict) and scope_value:
                scope = ", ".join(
                    f"{key}={value}" for key, value in sorted(scope_value.items())
                )
                break
    if scope:
        return f"{slug_title(report_type)} - {scope}"
    return slug_title(report_type)


def by_schema(artifacts: list[dict[str, Any]], schema: str) -> list[dict[str, Any]]:
    return [artifact for artifact in artifacts if schema_of(artifact) == schema]


def cited_artifact_selectors(
    notes: list[dict[str, Any]],
) -> tuple[set[str], set[str]]:
    artifact_ids: set[str] = set()
    schema_only: set[str] = set()
    for note in notes:
        data_sources = note.get("data_sources") or []
        if not isinstance(data_sources, list):
            continue
        for source in data_sources:
            if not isinstance(source, dict):
                continue
            artifact_id = source.get("artifact_id")
            schema = source.get("schema_version")
            if isinstance(artifact_id, str):
                artifact_ids.add(artifact_id)
            elif isinstance(schema, str):
                schema_only.add(schema)
    return artifact_ids, schema_only


def duplicate_dedupe_risk(
    schema: str,
    report_type: str,
) -> str | None:
    selection_sensitive_schemas = {
        "executive_posture": {POSTURE_SCHEMA, INDEX_SCHEMA, MOVER_SCHEMA},
        "soc_triage": {
            INDEX_SCHEMA,
            SCORECARD_SCHEMA,
            POSTURE_SCHEMA,
            MOVER_SCHEMA,
        },
        "control_review": {CONTROL_SCHEMA, POSTURE_SCHEMA, MOVER_SCHEMA},
        "scorecard_brief": {SCORECARD_SCHEMA, INDEX_SCHEMA},
        "crawler_governance": {
            SCORECARD_SCHEMA,
            INDEX_SCHEMA,
            POSTURE_SCHEMA,
            MOVER_SCHEMA,
        },
        "edge_ops_impact": {
            SCORECARD_SCHEMA,
            INDEX_SCHEMA,
            POSTURE_SCHEMA,
            MOVER_SCHEMA,
        },
    }
    if schema in selection_sensitive_schemas.get(report_type, set()):
        if schema == SCORECARD_SCHEMA and report_type in {
            "soc_triage",
            "crawler_governance",
            "edge_ops_impact",
        }:
            return "duplicates could affect scorecard input order or rendered rows"
        return "duplicates could affect report artifact selection"
    return None


def dedupe_artifact_bodies(
    artifacts: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    report_type: str,
    ctx: ReportContext,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for artifact in artifacts:
        groups.setdefault(duplicate_body_fingerprint(artifact), []).append(artifact)

    cited_ids, schema_only_citations = cited_artifact_selectors(notes)
    dropped_ids: set[str] = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        kept = group[0]
        duplicate_ids = [str(artifact["artifact_id"]) for artifact in group]
        schema = schema_of(kept)
        if any(
            ctx.artifact_id_explicit.get(artifact_id) for artifact_id in duplicate_ids
        ):
            raise ReportError(
                "Artifact bodies for "
                + ", ".join(duplicate_ids)
                + " are identical; duplicates with explicit artifact IDs cannot be deduplicated safely."
            )
        if cited_ids.intersection(duplicate_ids) or schema in schema_only_citations:
            raise ReportError(
                "Artifact bodies for "
                + ", ".join(duplicate_ids)
                + " are identical; analyst-note citations make deduplication ambiguous."
            )
        risk = duplicate_dedupe_risk(schema, report_type)
        if risk:
            raise ReportError(
                "Artifact bodies for "
                + ", ".join(duplicate_ids)
                + f" are identical; {risk}."
            )
        dropped = duplicate_ids[1:]
        dropped_ids.update(dropped)
        ctx.warn(
            "Ignored duplicate artifact bodies for "
            + ", ".join(dropped)
            + f"; kept {kept['artifact_id']}."
        )

    if not dropped_ids:
        return artifacts
    return [
        artifact
        for artifact in artifacts
        if str(artifact.get("artifact_id")) not in dropped_ids
    ]


def require_one(
    artifacts: list[dict[str, Any]], schema: str, report_type: str
) -> dict[str, Any]:
    matches = by_schema(artifacts, schema)
    if not matches:
        raise ReportError(f"{report_type} requires {schema}.")
    if len(matches) > 1:
        raise ReportError(f"{report_type} requires one {schema}; found {len(matches)}.")
    return matches[0]


COMPANION_COMPAT_FIELDS = (
    "scope",
    "current_window",
    "baseline_windows",
    "comparison_type",
    "table_used",
)


def companion_compatible(
    primary: dict[str, Any] | None,
    companion: dict[str, Any],
) -> tuple[bool, str | None]:
    if primary is None:
        return False, "no primary artifact carries compatibility metadata"
    for field in COMPANION_COMPAT_FIELDS:
        left = primary.get(field)
        right = companion.get(field)
        if not known(left) or not known(right):
            return False, f"missing {field} metadata on one side"
        if left != right:
            return False, f"conflict on {field}"
    return True, None


def filter_compatible_companion(
    primary: dict[str, Any] | None,
    companion: dict[str, Any] | None,
    label: str,
    ctx: ReportContext,
) -> dict[str, Any] | None:
    if companion is None:
        return None
    ok, reason = companion_compatible(primary, companion)
    if ok:
        return companion
    ctx.warn(
        f"Omitting optional {label} {companion.get('artifact_id')} from combined sections: {reason}."
    )
    return None


def validate_report_artifacts(
    report_type: str,
    artifacts: list[dict[str, Any]],
    ctx: ReportContext,
) -> dict[str, Any]:
    if report_type == "executive_posture":
        posture = require_one(artifacts, POSTURE_SCHEMA, report_type)
        index = first_or_warn(artifacts, INDEX_SCHEMA, report_type, ctx)
        mover = first_or_warn(artifacts, MOVER_SCHEMA, report_type, ctx)
        index = filter_compatible_companion(posture, index, "index", ctx)
        scorecards: list[dict[str, Any]] = []
        if index:
            scorecards = compatible_scorecards_for_index(
                index,
                by_schema(artifacts, SCORECARD_SCHEMA),
                ctx,
                required=False,
            )
        return {
            "posture": posture,
            "index": index,
            "scorecards": scorecards,
            "mover": filter_compatible_companion(posture, mover, "mover", ctx),
        }
    if report_type == "soc_triage":
        index = require_one(artifacts, INDEX_SCHEMA, report_type)
        scorecards = by_schema(artifacts, SCORECARD_SCHEMA)
        scorecards = compatible_scorecards_for_index(
            index, scorecards, ctx, required=bool(scorecards)
        )
        if not scorecards:
            ctx.warn(
                "SOC triage has only bot_scorecard_index.v1 and renders a degraded ranking-only report."
            )
        posture = first_or_warn(artifacts, POSTURE_SCHEMA, report_type, ctx)
        mover = first_or_warn(artifacts, MOVER_SCHEMA, report_type, ctx)
        return {
            "index": index,
            "scorecards": scorecards,
            "posture": filter_compatible_companion(index, posture, "posture", ctx),
            "mover": filter_compatible_companion(index, mover, "mover", ctx),
        }
    if report_type == "control_review":
        control = require_one(artifacts, CONTROL_SCHEMA, report_type)
        posture = first_or_warn(artifacts, POSTURE_SCHEMA, report_type, ctx)
        mover = first_or_warn(artifacts, MOVER_SCHEMA, report_type, ctx)
        return {
            "control": control,
            "posture": filter_compatible_companion(control, posture, "posture", ctx),
            "mover": filter_compatible_companion(control, mover, "mover", ctx),
        }
    if report_type == "scorecard_brief":
        scorecards = by_schema(artifacts, SCORECARD_SCHEMA)
        if not scorecards:
            raise ReportError(f"{report_type} requires {SCORECARD_SCHEMA}.")
        index = first_or_warn(artifacts, INDEX_SCHEMA, report_type, ctx)
        index_order_usable = False
        if index:
            scorecards, index_order_usable = (
                compatible_scorecards_for_index_with_order_status(
                    index, scorecards, ctx, required=True
                )
            )
        scorecard = scorecards[0]
        return {
            "scorecard": scorecard,
            "scorecards": scorecards,
            "index": index,
            "index_order_usable": index_order_usable,
            "is_fleet": bool(index or len(scorecards) > 1),
        }
    if report_type == "crawler_governance":
        scorecards = by_schema(artifacts, SCORECARD_SCHEMA)
        if not scorecards:
            raise ReportError(
                "crawler_governance requires bot_entity_scorecard.v1 artifacts or a scorecard packet."
            )
        index = first_or_warn(artifacts, INDEX_SCHEMA, report_type, ctx)
        index_order_usable = False
        if index:
            (
                scorecards,
                index_order_usable,
            ) = compatible_scorecards_for_index_with_order_status(
                index, scorecards, ctx, required=False
            )
        reference = index or (scorecards[0] if scorecards else None)
        posture = first_or_warn(artifacts, POSTURE_SCHEMA, report_type, ctx)
        mover = first_or_warn(artifacts, MOVER_SCHEMA, report_type, ctx)
        return {
            "scorecards": scorecards,
            "index": index,
            "index_order_usable": index_order_usable,
            "posture": filter_compatible_companion(reference, posture, "posture", ctx),
            "mover": filter_compatible_companion(reference, mover, "mover", ctx),
        }
    if report_type == "edge_ops_impact":
        scorecards = by_schema(artifacts, SCORECARD_SCHEMA)
        if not scorecards:
            raise ReportError(
                "edge_ops_impact requires bot_entity_scorecard.v1 artifacts or a scorecard packet."
            )
        index = first_or_warn(artifacts, INDEX_SCHEMA, report_type, ctx)
        index_order_usable = False
        if index:
            (
                scorecards,
                index_order_usable,
            ) = compatible_scorecards_for_index_with_order_status(
                index, scorecards, ctx, required=False
            )
        reference = index or (scorecards[0] if scorecards else None)
        posture = first_or_warn(artifacts, POSTURE_SCHEMA, report_type, ctx)
        mover = first_or_warn(artifacts, MOVER_SCHEMA, report_type, ctx)
        return {
            "scorecards": scorecards,
            "index": index,
            "index_order_usable": index_order_usable,
            "posture": filter_compatible_companion(reference, posture, "posture", ctx),
            "mover": filter_compatible_companion(reference, mover, "mover", ctx),
        }
    raise ReportError(f"Unsupported report type {report_type}.")


def known(value: Any) -> bool:
    return value not in (None, "", [], {})


def same_packet(
    left: dict[str, Any], right: dict[str, Any], ctx: ReportContext
) -> bool:
    left_parent = ctx.generated_child_parent.get(str(left.get("artifact_id")))
    right_parent = ctx.generated_child_parent.get(str(right.get("artifact_id")))
    return bool(left_parent and left_parent == right_parent)


def shared_metadata_matches(
    index: dict[str, Any], scorecard: dict[str, Any], ctx: ReportContext
) -> bool:
    if same_packet(index, scorecard, ctx):
        for field in (
            "scope",
            "current_window",
            "baseline_windows",
            "table_used",
            "comparison_type",
        ):
            left = index.get(field)
            right = scorecard.get(field)
            if known(left) and known(right) and left != right:
                raise ReportError(
                    f"Same-packet scorecard metadata mismatch for {field}."
                )
            if not known(left) or not known(right):
                ctx.warn(
                    f"{scorecard.get('artifact_id')} missing same-packet {field} metadata."
                )
        return True

    for field in ("scope", "current_window", "baseline_windows", "table_used"):
        left = index.get(field)
        right = scorecard.get(field)
        if not known(left) or not known(right):
            raise ReportError(
                f"Standalone scorecard pairing requires known {field} metadata."
            )
        if left != right:
            raise ReportError(f"Scorecard metadata mismatch for {field}.")

    left_comparison = index.get("comparison_type")
    right_comparison = scorecard.get("comparison_type")
    if known(left_comparison) != known(right_comparison):
        raise ReportError(
            "Standalone scorecard pairing requires matching comparison_type metadata when present."
        )
    if known(left_comparison) and left_comparison != right_comparison:
        raise ReportError("Scorecard metadata mismatch for comparison_type.")
    return True


def compatible_scorecards_for_index_with_order_status(
    index: dict[str, Any],
    scorecards: list[dict[str, Any]],
    ctx: ReportContext,
    *,
    required: bool,
) -> tuple[list[dict[str, Any]], bool]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for card in scorecards:
        if not known(card.get("entity_type")) or not known(card.get("entity")):
            continue
        key = (str(card.get("entity_type")), str(card.get("entity")))
        existing = by_key.get(key)
        if existing is not None:
            raise ReportError(
                "Multiple scorecards share entity_type/entity "
                f"{key[0]}={key[1]}; pairing with an index would be ambiguous."
            )
        by_key[key] = card
    compatible: list[dict[str, Any]] = []
    for row in index.get("ranked_entities", []):
        key = (str(row.get("entity_type")), str(row.get("entity")))
        card = by_key.get(key)
        if not card:
            continue
        if shared_metadata_matches(index, card, ctx):
            compatible.append(card)
    if required and scorecards and not compatible:
        raise ReportError("No scorecards are compatible with the selected index.")
    if scorecards and not compatible:
        ctx.warn(
            "No scorecards were compatible with the selected index; using input order."
        )
        return scorecards, False
    return compatible, bool(compatible)


def compatible_scorecards_for_index(
    index: dict[str, Any],
    scorecards: list[dict[str, Any]],
    ctx: ReportContext,
    *,
    required: bool,
) -> list[dict[str, Any]]:
    compatible, _ = compatible_scorecards_for_index_with_order_status(
        index, scorecards, ctx, required=required
    )
    return compatible


def first_or_warn(
    artifacts: list[dict[str, Any]],
    schema: str,
    report_type: str,
    ctx: ReportContext,
) -> dict[str, Any] | None:
    matches = by_schema(artifacts, schema)
    if len(matches) > 1:
        raise ReportError(
            f"{report_type} cannot select between multiple {schema} artifacts."
        )
    return matches[0] if matches else None


def scan_metadata_warnings(artifacts: list[dict[str, Any]], ctx: ReportContext) -> None:
    for artifact in artifacts:
        schema = schema_of(artifact)
        aid = artifact.get("artifact_id")
        if schema in {POSTURE_SCHEMA, SCORECARD_SCHEMA, INDEX_SCHEMA}:
            if not artifact.get("current_window"):
                ctx.warn(f"{aid} missing current_window metadata.")
            if not artifact.get("baseline_windows"):
                ctx.warn(f"{aid} missing baseline_windows metadata.")
        elif schema == CONTROL_SCHEMA:
            if not artifact.get("before_window"):
                ctx.warn(f"{aid} missing before_window metadata.")
            if not artifact.get("after_window"):
                ctx.warn(f"{aid} missing after_window metadata.")
            effects = artifact.get("target_effects") or []
            has_expected = any(
                isinstance(effect, dict)
                and "expected" in effect
                and effect.get("expected") is not None
                for effect in effects
            )
            basis = artifact.get("expected_basis")
            if has_expected and (
                not isinstance(basis, str) or basis not in CONTROL_EXPECTED_BASES
            ):
                ctx.warn(
                    f"{aid} missing or unknown expected_basis with expected target effects."
                )
            if basis in {"before_window", "external_model"} and not artifact.get(
                "expected_window"
            ):
                ctx.warn(
                    f"{aid} missing expected_window metadata for expected_basis {basis}."
                )
        elif schema == MOVER_SCHEMA:
            if not artifact.get("dimension"):
                ctx.warn(f"{aid} missing mover dimension metadata.")
            if not artifact.get("metric"):
                ctx.warn(f"{aid} missing mover metric metadata.")


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    output = [
        "| " + " | ".join(md_escape(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        output.append("| " + " | ".join(md_escape(cell) for cell in row) + " |")
    return "\n".join(output)


def limited_rows(
    rows: list[Any], limit: int, label: str, ctx: ReportContext
) -> list[Any]:
    if limit > 0 and len(rows) > limit:
        ctx.warn(
            f"Showing {limit} of {len(rows)} available {label}; display limit omitted {len(rows) - limit}."
        )
        return rows[:limit]
    return rows


def window_text(artifact: dict[str, Any]) -> str:
    parts = []
    labels = {
        "current_window": "current",
        "before_window": "before",
        "after_window": "after",
        "expected_window": "expected",
    }
    for key in ("current_window", "before_window", "after_window", "expected_window"):
        value = artifact.get(key)
        if isinstance(value, dict):
            parts.append(f"{labels[key]} {human_window_range(value)}")
        elif value:
            parts.append(f"{labels[key]} {stringify(value)}")
    baselines = artifact.get("baseline_windows")
    if isinstance(baselines, list) and baselines and isinstance(baselines[0], dict):
        parts.append(f"baseline {human_window_range(baselines[0])}")
    elif baselines:
        parts.append(f"baseline {stringify(baselines)}")
    return "; ".join(parts) if parts else "unavailable"


def evidence_window_summary(artifact: dict[str, Any]) -> str:
    current = artifact.get("current_window")
    baselines = artifact.get("baseline_windows")
    parts: list[str] = []
    if isinstance(current, dict):
        parts.append(f"current window {human_window_range(current)}")
    if isinstance(baselines, list) and baselines and isinstance(baselines[0], dict):
        parts.append(f"baseline window {human_window_range(baselines[0])}")
    return "; ".join(parts) if parts else "unavailable"


def artifact_display_name(artifact: dict[str, Any]) -> str:
    title = artifact.get("title")
    if isinstance(title, str) and title.strip():
        return title
    schema = artifact.get("schema_version")
    if schema == POSTURE_SCHEMA:
        return "Posture movement"
    if schema == TIMESERIES_SCHEMA:
        return "Hourly trend evidence"
    return str(artifact.get("artifact_id") or "Artifact")


def selected_artifacts(selected: dict[str, Any]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for value in selected.values():
        if value is None:
            continue
        if isinstance(value, list):
            collected.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            collected.append(value)
    return collected


def format_artifact_scope(scope: dict[str, Any]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(scope.items()))


def resolve_scope_display(
    scope_label: str | None,
    selected: dict[str, Any],
    ctx: ReportContext,
) -> str:
    if scope_label:
        return str(scope_label)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for artifact in selected_artifacts(selected):
        scope = artifact.get("scope")
        if not isinstance(scope, dict) or not scope:
            continue
        fingerprint = json.dumps(scope, sort_keys=True, separators=(",", ":"))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(scope)
    if not unique:
        ctx.warn(
            "Scope unavailable: no wrapper scope_label and no selected artifact carries scope metadata."
        )
        return "unavailable"
    if len(unique) > 1:
        ctx.warn(
            "Scope mixed: selected artifacts disagree on scope metadata; rendered as mixed."
        )
        return "mixed"
    return format_artifact_scope(unique[0])


def render_markdown(
    title: str,
    report_type: str,
    selected: dict[str, Any],
    all_artifacts: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    limit: int,
    ctx: ReportContext,
    *,
    scope_label: str | None = None,
    include_metadata: bool = True,
) -> str:
    scope_text = resolve_scope_display(scope_label, selected, ctx)
    parts = [
        f"# {md_escape(title)}",
    ]
    if include_metadata:
        parts.extend(
            [
                f"Report type: `{report_type}`",
                f"Scope: {md_escape(scope_text)}",
                "",
            ]
        )
    parts.append(md_analyst_notes(notes, all_artifacts, ctx))
    if report_type == "executive_posture":
        parts.append(md_executive(selected, limit, ctx))
    elif report_type == "soc_triage":
        parts.append(md_soc(selected, limit, ctx))
    elif report_type == "control_review":
        parts.append(md_control(selected, limit, ctx))
    elif report_type == "scorecard_brief":
        parts.append(md_scorecard_brief(selected, ctx))
    elif report_type == "crawler_governance":
        parts.append(
            md_domain_report(
                "Crawler Governance", selected, limit, ctx, crawler_features_for_card
            )
        )
    elif report_type == "edge_ops_impact":
        parts.append(
            md_domain_report(
                "Edge/Ops Impact", selected, limit, ctx, edge_ops_features_for_card
            )
        )
    parts.append(md_evidence_limits(all_artifacts, ctx))
    if ctx.warnings:
        parts.append(
            "## Warnings\n\n"
            + "\n".join(f"- {md_escape(warning)}" for warning in ctx.warnings)
        )
    return "\n\n".join(part for part in parts if part.strip()) + "\n"


def md_executive(selected: dict[str, Any], limit: int, ctx: ReportContext) -> str:
    posture = selected["posture"]
    metrics = limited_rows(posture.get("metrics", []), limit, "posture metrics", ctx)
    scorecards = selected.get("scorecards") or []
    rows = [
        [
            human_metric_name(metric.get("name")),
            human_number(metric.get("current")),
            human_number(metric.get("baseline")),
            human_delta(metric.get("absolute_delta")),
            human_number(metric.get("pct_change"), percent=True),
            metric.get("direction"),
            metric.get("confidence"),
        ]
        for metric in metrics
    ]
    parts = [
        "## Executive Summary",
        "\n".join(f"- {md_escape(line)}" for line in executive_summary_lines(metrics)),
        "## Metric Deltas",
        md_table(
            [
                "Metric",
                "Current",
                "Baseline",
                "Delta",
                "Pct change",
                "Direction",
                "Confidence",
            ],
            rows,
        )
        if rows
        else "No metric deltas available.",
    ]
    index = selected.get("index")
    if index:
        parts.extend(["## Top Scorecard Ranking", md_ranking(index, limit, ctx)])
        if scorecards:
            parts.extend(
                [
                    "## Lens Rollup",
                    md_executive_scorecard_rollup(scorecards, limit, ctx),
                    "## Domain Score Matrix",
                    md_domain_matrix(scorecards, limit, ctx),
                ]
            )
        else:
            parts.extend(
                [
                    "## Lens Rollup",
                    "Scorecard index is available, but compatible scorecard details were not provided; lens/domain rollups are unavailable.",
                ]
            )
    mover = selected.get("mover")
    if mover:
        parts.extend(["## Movers", md_movers(mover, limit, ctx)])
    return "\n\n".join(parts)


def md_soc(selected: dict[str, Any], limit: int, ctx: ReportContext) -> str:
    parts = ["## Top Risky Entities", md_ranking(selected["index"], limit, ctx)]
    scorecards = selected.get("scorecards") or []
    if scorecards:
        parts.extend(
            ["## Scorecard Analysis", md_scorecard_analysis(scorecards, limit, ctx)]
        )
        parts.extend(
            ["## Domain Score Matrix", md_domain_matrix(scorecards, limit, ctx)]
        )
        parts.extend(
            [
                "## Security Evidence Notes",
                md_feature_list(scorecards, {"security_evidence"}, None, limit, ctx),
            ]
        )
        missing_section = md_missing_feature_evidence(scorecards, limit, ctx)
        if missing_section:
            parts.extend(["## Missing Feature Evidence", missing_section])
        confidence_section = md_confidence_notes(scorecards, limit, ctx)
        if confidence_section:
            parts.extend(["## Confidence Notes", confidence_section])
    return "\n\n".join(parts)


def md_scorecard_analysis(
    scorecards: list[dict[str, Any]], limit: int, ctx: ReportContext
) -> str:
    sections: list[str] = []
    for card in limited_rows(scorecards, limit, "scorecard analysis entities", ctx):
        lines = [
            f"### {md_escape(card.get('entity'))}",
            "",
            md_table(
                ["Score", "Band", "Primary domain", "Confidence"],
                [
                    [
                        card.get("score"),
                        card.get("band"),
                        card.get("primary_domain"),
                        card.get("confidence"),
                    ]
                ],
            ),
        ]
        evidence = [
            item
            for item in card.get("evidence_summary", [])
            if item is not None and str(item) != ""
        ]
        if evidence:
            lines.extend(
                [
                    "",
                    "**Evidence Summary**",
                    "",
                    "\n".join(f"- {md_escape(item)}" for item in evidence),
                ]
            )
        features = card.get("features") or []
        if features:
            lines.extend(["", "**Evaluated Features**", "", md_feature_rows(features)])
        steps = [
            step["detail"] if isinstance(step, dict) else step
            for step in card.get("recommended_next_steps", [])
            if step is not None and str(step) != ""
        ]
        steps = [step for step in steps if step]
        if steps:
            lines.extend(
                [
                    "",
                    "**Recommended Next Steps**",
                    "",
                    "\n".join(f"- {md_escape(step)}" for step in steps),
                ]
            )
        if not evidence and not features and not steps:
            lines.extend(
                [
                    "",
                    "No scorecard narrative fields were emitted for this entity.",
                ]
            )
        sections.append("\n".join(lines))
    return "\n\n".join(sections) if sections else "No scorecard analysis available."


def md_missing_feature_evidence(
    scorecards: list[dict[str, Any]], limit: int, ctx: ReportContext
) -> str:
    groups = []
    for card in scorecards:
        missing = card.get("not_evaluated_features") or []
        if isinstance(missing, list) and missing:
            groups.append((card, missing))
    if not groups:
        return ""
    lines = []
    for card, missing in limited_rows(groups, limit, "missing-feature groups", ctx):
        lines.append(
            f"### {md_escape(card.get('entity'))}\n\n{md_missing_rows(missing)}"
        )
    return "\n\n".join(lines)


def md_confidence_notes(
    scorecards: list[dict[str, Any]], limit: int, ctx: ReportContext
) -> str:
    rows: list[list[Any]] = []
    for card in scorecards:
        reasons = card.get("confidence_reasons") or []
        confidence = card.get("confidence")
        if not confidence and not reasons:
            continue
        rows.append(
            [
                card.get("entity_type"),
                card.get("entity"),
                confidence or "unavailable",
                ", ".join(str(reason) for reason in reasons)
                if reasons
                else "unavailable",
            ]
        )
    if not rows:
        return ""
    rows = limited_rows(rows, limit, "confidence rows", ctx)
    return md_table(["Entity type", "Entity", "Confidence", "Confidence reasons"], rows)


def md_control(selected: dict[str, Any], limit: int, ctx: ReportContext) -> str:
    control = selected["control"]
    rows = []
    for effect in limited_rows(
        control.get("target_effects", []), limit, "control effects", ctx
    ):
        rows.append(
            [
                human_metric_name(effect.get("metric")),
                human_number(effect.get("before")),
                human_number(effect.get("after")),
                human_number(effect.get("expected")),
                human_delta(effect.get("absolute_delta_vs_expected")),
                human_number(effect.get("pct_change_vs_expected"), percent=True),
                effect.get("status"),
                effect.get("confidence"),
            ]
        )
    parts = [
        "## Control Review Summary",
        "Effectiveness review based on emitted artifact fields. The artifact alone is not causal proof.",
        f"Target: {md_escape(control.get('target', {}))}",
        f"Windows: {md_escape(window_text(control))}",
        "## Before/After/Expected",
        md_table(
            [
                "Metric",
                "Before",
                "After",
                "Expected",
                "Delta vs expected",
                "Pct change",
                "Status",
                "Confidence",
            ],
            rows,
        )
        if rows
        else "No target effects available.",
        "## Collateral Checks",
        md_control_check_table(
            control.get("collateral_checks") or [], limit, ctx, "collateral checks"
        ),
        "## Displacement Checks",
        md_control_check_table(
            control.get("displacement_checks") or [], limit, ctx, "displacement checks"
        ),
    ]
    basis = control.get("expected_basis")
    if basis:
        basis_label = stringify(basis).replace("_", " ")
        parts.extend(
            [
                "## Confidence",
                f"Expected basis: {md_escape(basis_label)}. This is an effectiveness review, not proof of cause.",
            ]
        )
    return "\n\n".join(parts)


def md_control_check_table(
    checks: list[Any], limit: int, ctx: ReportContext, label: str
) -> str:
    filtered = [check for check in checks if isinstance(check, dict)]
    if not filtered:
        return f"No {label} reported."
    limited = limited_rows(filtered, limit, label, ctx)
    rows = [
        [
            check.get("metric") or check.get("name"),
            check.get("before"),
            check.get("after"),
            check.get("absolute_delta") or check.get("delta"),
            check.get("pct_change"),
            check.get("status"),
            check.get("confidence"),
        ]
        for check in limited
    ]
    return md_table(
        ["Metric", "Before", "After", "Delta", "Pct change", "Status", "Confidence"],
        rows,
    )


def md_scorecard_brief(selected: dict[str, Any], ctx: ReportContext) -> str:
    card = selected["scorecard"]
    index = selected.get("index") or {}
    rank = None
    total_ranked = index.get("total_ranked_entities") or index.get("result_row_count")
    entity_type_label = display_label(card.get("entity_type"))
    for row in index.get("ranked_entities", []):
        if (
            isinstance(row, dict)
            and row.get("entity_type") == card.get("entity_type")
            and row.get("entity") == card.get("entity")
        ):
            rank = row.get("rank")
            break
    if rank is not None and total_ranked:
        rank_display = (
            f"{human_number(rank)} of {human_number(total_ranked)} scored "
            f"{entity_type_label} entities"
        )
    elif rank is not None:
        rank_display = f"{human_number(rank)} in scored entity set"
    else:
        rank_display = "unavailable"
    summary_rows = [
        ["Scored dimension", entity_type_label],
        ["Selected entity", card.get("entity")],
        ["Rank in scored set", rank_display],
        ["Current health score", human_number(card.get("score"))],
        ["Primary risk domain", display_label(card.get("primary_domain"))],
        ["Evidence confidence", card.get("confidence")],
    ]
    parts = [
        "## Selected Entity Context",
        (
            f"This brief explains one selected `{md_escape(entity_type_label)}` "
            "from the larger scored entity set."
        ),
        md_table(["Field", "Value"], summary_rows),
        "## Domain Scores",
        md_table(
            ["Domain", "Score"],
            [
                [display_label(domain), human_number(score)]
                for domain, score in (card.get("domain_scores") or {}).items()
            ],
        ),
        "## Evaluated Feature Evidence",
        md_feature_rows(card.get("features", []))
        if card.get("features")
        else "No evaluated features crossed thresholds.",
        "## Missing Scorecard Inputs",
        md_missing_rows(card.get("not_evaluated_features", []))
        if card.get("not_evaluated_features")
        else "No missing feature inputs reported.",
    ]
    steps = card.get("recommended_next_steps")
    if isinstance(steps, list) and steps:
        normalized = [
            step["detail"] if isinstance(step, dict) else step for step in steps
        ]
        parts.extend(
            [
                "## Recommended Next Steps",
                "\n".join(f"- {md_escape(step)}" for step in normalized),
            ]
        )
    producer = _producer_limit_bullet(card) or _producer_limit_bullet(index)
    if producer:
        parts.extend(["## Rowset Limits", md_escape(producer)])
    return "\n\n".join(parts)


def md_domain_report(
    heading: str,
    selected: dict[str, Any],
    limit: int,
    ctx: ReportContext,
    feature_selector: Any,
) -> str:
    scorecards = selected.get("scorecards") or []
    index = selected.get("index")
    relevant: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    missing_count = 0
    for card in ordered_scorecards(scorecards, index):
        features, missing = feature_selector(card)
        missing_count += len(missing)
        if features:
            relevant.append((card, features))
    if not relevant:
        ctx.warn(
            f"{heading} report has scorecards but no eligible evaluated relevant evidence."
        )
        if missing_count:
            ctx.warn(
                f"{heading} report found {missing_count} relevant missing feature inputs."
            )
        return f"## {heading} Summary\n\nNo relevant {heading.lower()} evidence available. This is not evidence that posture is safe."
    limited = limited_rows(relevant, limit, f"{heading.lower()} entities", ctx)
    order_label = (
        "Rows follow scorecard index order."
        if index and selected.get("index_order_usable")
        else "Rows follow normalized scorecard input order; this is not a ranking."
    )
    rows = [
        [
            card.get("entity_type"),
            card.get("entity"),
            card.get("score"),
            ", ".join(str(feature.get("name")) for feature in features),
            card.get("confidence"),
        ]
        for card, features in limited
    ]
    parts = [
        f"## {heading} Summary",
        order_label,
        md_table(
            ["Entity type", "Entity", "Score", "Relevant features", "Confidence"], rows
        ),
        f"## {heading} Evidence",
    ]
    for card, features in limited:
        parts.append(
            f"### {md_escape(card.get('entity'))}\n\n" + md_feature_rows(features)
        )
    if missing_count:
        ctx.warn(
            f"{heading} report found {missing_count} relevant missing feature inputs."
        )
    return "\n\n".join(parts)


def md_ranking(index: dict[str, Any], limit: int, ctx: ReportContext) -> str:
    ranked = limited_rows(
        index.get("ranked_entities", []), limit, "ranked entities", ctx
    )
    rows = [
        [
            row.get("rank"),
            row.get("entity_type"),
            row.get("entity"),
            row.get("score"),
            row.get("band"),
            row.get("primary_domain"),
            row.get("confidence"),
        ]
        for row in ranked
    ]
    return (
        md_table(
            [
                "Rank",
                "Entity type",
                "Entity",
                "Score",
                "Band",
                "Primary domain",
                "Confidence",
            ],
            rows,
        )
        if rows
        else "No ranked entities available."
    )


def md_movers(mover: dict[str, Any], limit: int, ctx: ReportContext) -> str:
    movers = limited_rows(mover.get("movers", []), limit, "movers", ctx)
    rows = [
        [
            row.get("value"),
            row.get("metric"),
            row.get("current"),
            row.get("baseline"),
            row.get("absolute_delta"),
            row.get("contribution_pct"),
            row.get("confidence"),
        ]
        for row in movers
    ]
    return (
        md_table(
            [
                "Value",
                "Metric",
                "Current",
                "Baseline",
                "Delta",
                "Contribution pct",
                "Confidence",
            ],
            rows,
        )
        if rows
        else "No mover attribution available."
    )


def md_domain_matrix(
    scorecards: list[dict[str, Any]], limit: int, ctx: ReportContext
) -> str:
    domains = domain_score_order(scorecards)
    rows = []
    for card in limited_rows(scorecards, limit, "scorecards", ctx):
        domain_scores = card.get("domain_scores") or {}
        rows.append(
            [card.get("entity"), card.get("score")]
            + [domain_scores.get(domain, "unavailable") for domain in domains]
        )
    return (
        md_table(["Entity", "Total score"] + domains, rows)
        if rows
        else "No scorecard domain scores available."
    )


def md_executive_scorecard_rollup(
    scorecards: list[dict[str, Any]], limit: int, ctx: ReportContext
) -> str:
    domain_totals: dict[str, float] = {}
    primary_counts: dict[str, int] = {}
    caveats: dict[str, int] = {}
    for card in scorecards:
        primary = str(card.get("primary_domain") or "none")
        primary_counts[primary] = primary_counts.get(primary, 0) + 1
        domain_scores = card.get("domain_scores") or {}
        if isinstance(domain_scores, dict):
            for domain, score in domain_scores.items():
                numeric = to_float(score)
                if numeric is not None:
                    domain_text = str(domain)
                    domain_totals[domain_text] = (
                        domain_totals.get(domain_text, 0.0) + numeric
                    )
        for reason in card.get("confidence_reasons") or []:
            reason_text = str(reason)
            if reason_text in {
                "feature_input_missing",
                "siem_unavailable",
                "source_coverage_caveat",
                "sparse_counts",
            }:
                caveats[reason_text] = caveats.get(reason_text, 0) + 1

    domain_rows = [
        [domain, clean_display(score)]
        for domain, score in sorted(
            domain_totals.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    primary_rows = [
        [domain, count]
        for domain, count in sorted(
            primary_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    caveat_rows = [
        [reason, count]
        for reason, count in sorted(
            caveats.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    parts = [
        "Scorecard rollup uses emitted scorecard fields only; it does not create executive-only features.",
        "### Domain Totals",
        md_table(
            ["Domain", "Total score"],
            limited_rows(domain_rows, limit, "domain rollup rows", ctx),
        )
        if domain_rows
        else "No numeric domain scores available.",
        "### Primary Lens Counts",
        md_table(
            ["Primary domain", "Entities"],
            limited_rows(primary_rows, limit, "primary lens rows", ctx),
        )
        if primary_rows
        else "No primary domain values available.",
        "### Caveats",
        md_table(
            ["Caveat", "Entities"], limited_rows(caveat_rows, limit, "caveat rows", ctx)
        )
        if caveat_rows
        else "No scorecard caveats reported.",
    ]
    return "\n\n".join(parts)


def domain_score_order(scorecards: list[dict[str, Any]]) -> list[str]:
    domains: list[str] = []
    seen: set[str] = set()
    for card in scorecards:
        domain_scores = card.get("domain_scores")
        if not isinstance(domain_scores, dict):
            continue
        for domain in domain_scores:
            domain_text = str(domain)
            if domain_text in seen:
                continue
            seen.add(domain_text)
            domains.append(domain_text)
    return domains


def md_feature_list(
    scorecards: list[dict[str, Any]],
    domains: set[str],
    names: set[str] | None,
    limit: int,
    ctx: ReportContext,
) -> str:
    selected = []
    for card in scorecards:
        features = [
            feature
            for feature in card.get("features", [])
            if feature.get("domain") in domains
            and (names is None or feature.get("name") in names)
        ]
        if features:
            selected.append((card, features))
    if not selected:
        return "No matching feature evidence available."
    lines = []
    for card, features in limited_rows(selected, limit, "feature evidence groups", ctx):
        lines.append(
            f"### {md_escape(card.get('entity'))}\n\n{md_feature_rows(features)}"
        )
    return "\n\n".join(lines)


def md_feature_rows(features: list[dict[str, Any]]) -> str:
    rows = []
    for feature in features:
        feature_label, condition = rule_label_parts(feature.get("name"))
        rows.append(
            [
                display_label(feature.get("domain")),
                feature_label,
                condition,
                feature.get("points"),
                feature.get("evidence"),
            ]
        )
    return md_table(["Domain", "Feature", "Condition", "Points", "Evidence"], rows)


def md_missing_rows(missing: list[dict[str, Any]]) -> str:
    rows = []
    for feature in missing:
        feature_label, condition = rule_label_parts(feature.get("name"))
        rows.append(
            [
                display_label(feature.get("domain")),
                feature_label,
                condition,
                ", ".join(str(item) for item in feature.get("missing_inputs", [])),
                display_label(feature.get("reason")),
            ]
        )
    return md_table(
        ["Domain", "Feature", "Condition", "Missing inputs", "Reason"], rows
    )


def ordered_scorecards(
    scorecards: list[dict[str, Any]], index: dict[str, Any] | None
) -> list[dict[str, Any]]:
    if not index:
        return scorecards
    by_key = {
        (str(card.get("entity_type")), str(card.get("entity"))): card
        for card in scorecards
    }
    ordered = []
    for row in index.get("ranked_entities", []):
        key = (str(row.get("entity_type")), str(row.get("entity")))
        if key in by_key:
            ordered.append(by_key.pop(key))
    ordered.extend(by_key.values())
    return ordered


def crawler_specific_provenance(card: dict[str, Any], feature: dict[str, Any]) -> bool:
    allowed = {"crawler", "good_bot", "ai_crawler"}
    provenance = card.get("feature_provenance")
    name = str(feature.get("name"))
    if isinstance(provenance, dict):
        feature_provenance = provenance.get(name)
        if isinstance(feature_provenance, dict):
            rowset = feature_provenance.get("rowset_scope")
            if isinstance(rowset, dict) and rowset.get("population") in allowed:
                return True
    rowset = card.get("rowset_scope")
    return isinstance(rowset, dict) and rowset.get("population") in allowed


def crawler_provenance_gaps(card: dict[str, Any]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    if schema_of(card) != SCORECARD_SCHEMA:
        return gaps
    for feature in card.get("features", []):
        if not isinstance(feature, dict):
            continue
        if feature.get("domain") != "crawler_governance":
            continue
        name = str(feature.get("name"))
        if name not in GENERIC_CRAWLER_RATE_FEATURES:
            continue
        if crawler_specific_provenance(card, feature):
            continue
        gaps.append(feature)
    return gaps


def crawler_features_for_card(
    card: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    features: list[dict[str, Any]] = []
    for feature in card.get("features", []):
        if feature.get("domain") != "crawler_governance":
            continue
        name = str(feature.get("name"))
        if name not in CRAWLER_FEATURES:
            continue
        if name in GENERIC_CRAWLER_RATE_FEATURES and not crawler_specific_provenance(
            card, feature
        ):
            continue
        features.append(feature)
    missing = [
        item
        for item in card.get("not_evaluated_features", [])
        if item.get("domain") == "crawler_governance"
        and item.get("name") in CRAWLER_FEATURES
    ]
    return features, missing


def edge_ops_features_for_card(
    card: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    features = [
        feature
        for feature in card.get("features", [])
        if feature.get("domain") in EDGE_OPS_DOMAINS
        and feature.get("name") in EDGE_OPS_FEATURES
    ]
    missing = [
        item
        for item in card.get("not_evaluated_features", [])
        if item.get("domain") in EDGE_OPS_DOMAINS
        and item.get("name") in EDGE_OPS_FEATURES
    ]
    return features, missing


def _format_scope_value(scope: Any) -> str:
    if isinstance(scope, dict) and scope:
        return ", ".join(f"{key}={value}" for key, value in sorted(scope.items()))
    if scope in (None, "", {}, []):
        return "unavailable"
    return stringify(scope)


def _format_list_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(stringify(item) for item in value) if value else "unavailable"
    if value in (None, "", [], {}):
        return "unavailable"
    return stringify(value)


def _producer_limit_bullet(artifact: dict[str, Any]) -> str | None:
    fields = (
        "result_row_count",
        "producer_limit",
        "result_truncated",
        "source_row_count",
        "total_ranked_entities",
    )
    parts = [
        f"{field}={stringify(artifact[field])}" for field in fields if field in artifact
    ]
    if not parts:
        return None
    return "Producer limits: " + ", ".join(parts)


def _source_population_caveat(artifact: dict[str, Any]) -> str | None:
    has_truncation_context = any(
        field in artifact
        for field in ("producer_limit", "result_row_count", "result_truncated")
    )
    if not has_truncation_context:
        return None
    if any(
        field in artifact for field in ("source_row_count", "total_ranked_entities")
    ):
        return None
    return (
        "Source population caveat: producer did not provide full source-population metadata;"
        " counts reflect emitted artifacts only, not the upstream population."
    )


def md_evidence_limits(artifacts: list[dict[str, Any]], ctx: ReportContext) -> str:
    sections: list[str] = ["## Evidence Limits"]
    for artifact in artifacts:
        aid = artifact.get("artifact_id") or "unavailable"
        schema = artifact.get("schema_version") or "unavailable"
        if schema == POSTURE_SCHEMA:
            bullets = [
                "- This is a movement report. It does not identify root cause by itself.",
            ]
            sections.append(
                f"### {md_escape(artifact_display_name(artifact))}\n\n"
                + "\n".join(bullets)
            )
            continue
        if schema == TIMESERIES_SCHEMA:
            metrics = artifact.get("metrics")
            metric_count = len(metrics) if isinstance(metrics, list) else 0
            is_control_trend = (
                artifact.get("title") == "Control Review Trends"
                or artifact.get("report_type") == "control_review"
            )
            comparison_label = (
                "after and expected windows"
                if is_control_trend
                else "current and prior windows"
            )
            exact_label = (
                "control effects table" if is_control_trend else "metric deltas table"
            )
            bullets = [
                f"- Trend cards: {metric_count} hourly metric series comparing {comparison_label}.",
                f"- Trend cards show shape and direction; exact aggregate values are in the {exact_label}.",
            ]
            sections.append(
                f"### {md_escape(artifact_display_name(artifact))}\n\n"
                + "\n".join(bullets)
            )
            continue
        bullets: list[str] = [f"- Schema: {md_escape(schema)}"]
        parent_id = artifact.get("parent_artifact_id")
        if parent_id:
            pointer = artifact.get("parent_json_pointer")
            parent_line = f"- Parent: {md_escape(parent_id)}"
            if pointer:
                parent_line += f" at {md_escape(pointer)}"
            bullets.append(parent_line)
        bullets.append(
            f"- Table: {md_escape(artifact.get('table_used') or 'unavailable')}"
        )
        bullets.append(
            f"- Scope: {md_escape(_format_scope_value(artifact.get('scope')))}"
        )
        bullets.append(
            f"- Confidence: {md_escape(artifact.get('confidence') or 'unavailable')}"
        )
        bullets.append(
            f"- Confidence reasons: {md_escape(_format_list_value(artifact.get('confidence_reasons')))}"
        )
        bullets.append(
            f"- Interpretation constraints: {md_escape(_format_list_value(artifact.get('interpretation_constraints')))}"
        )
        windows_text = window_text(artifact)
        if windows_text != "unavailable":
            bullets.append(f"- Windows: {md_escape(windows_text)}")
        not_evaluated = artifact.get("not_evaluated_features")
        if isinstance(not_evaluated, list) and not_evaluated:
            bullets.append("- Not-evaluated features:")
            for item in not_evaluated:
                if not isinstance(item, dict):
                    continue
                domain = item.get("domain") or "unavailable"
                name = item.get("name") or "unavailable"
                missing = ", ".join(
                    str(missing_input)
                    for missing_input in item.get("missing_inputs", [])
                )
                reason = item.get("reason") or "unavailable"
                missing_text = missing or "unavailable"
                bullets.append(
                    f"  - {md_escape(domain)} / {md_escape(name)}"
                    f" (missing inputs: {md_escape(missing_text)}; reason: {md_escape(reason)})"
                )
            if schema == SCORECARD_SCHEMA and isinstance(
                artifact.get("domain_scores"), dict
            ):
                domains = sorted(
                    {
                        str(item.get("domain"))
                        for item in not_evaluated
                        if isinstance(item, dict) and item.get("domain")
                    }
                )
                if domains:
                    bullets.append(
                        "- Domain score ambiguity: emitted numeric domain scores are rendered as-is; "
                        "missing inputs remain unresolved for "
                        + md_escape(", ".join(domains))
                        + "."
                    )
        provenance_gaps = crawler_provenance_gaps(artifact)
        if provenance_gaps:
            bullets.append("- Crawler provenance gaps:")
            for feature in provenance_gaps:
                name = feature.get("name") or "unavailable"
                bullets.append(
                    f"  - {md_escape(name)}: structured `rowset_scope`/`feature_provenance` "
                    "population is missing or non-crawler; generic 429/5xx feature was not rendered as a crawler finding."
                )
        producer_line = _producer_limit_bullet(artifact)
        if producer_line:
            bullets.append(f"- {md_escape(producer_line)}")
        caveat = _source_population_caveat(artifact)
        if caveat:
            bullets.append(f"- {md_escape(caveat)}")
        sections.append(f"### Artifact {md_escape(aid)}\n\n" + "\n".join(bullets))
    sections.append(
        "Reports use emitted artifact fields only. Missing evidence is unavailable, not zero or safe."
    )
    return "\n\n".join(sections)


def json_pointer_get(value: Any, pointer: str) -> Any:
    return json_pointer_resolve(value, pointer)[1]


def _encode_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _list_index_from_token(token: str, length: int) -> int:
    if re.fullmatch(r"0|[1-9][0-9]*", token):
        index = int(token)
    else:
        raise KeyError(token)
    if index < 0 or index >= length:
        raise KeyError(token)
    return index


def json_pointer_resolve(value: Any, pointer: str) -> tuple[str, Any]:
    if pointer == "":
        return "", value
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise KeyError(pointer)
    current = value
    normalized_tokens: list[str] = []
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            index = _list_index_from_token(token, len(current))
            normalized_tokens.append(str(index))
            current = current[index]
        elif isinstance(current, dict):
            current = current[token]
            normalized_tokens.append(_encode_pointer_token(token))
        else:
            raise KeyError(pointer)
    return "/" + "/".join(normalized_tokens), current


def validate_analyst_notes(
    notes: list[dict[str, Any]], artifacts: list[dict[str, Any]]
) -> None:
    for index, note in enumerate(notes, start=1):
        note_id = note.get("note_id", index)
        text = note.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ReportError(f"Analyst note {note_id} must include non-empty text.")
        author = note.get("author_type")
        if author not in {"llm", "analyst"}:
            raise ReportError(f"Analyst note {note_id} has unsupported author_type.")
        sources = note.get("data_sources", [])
        if sources is None:
            sources = []
        if not isinstance(sources, list):
            raise ReportError(f"Analyst note {note_id} data_sources must be an array.")
        for source in sources:
            if not isinstance(source, dict):
                raise ReportError(
                    f"Analyst note {note_id} data_sources entries must be objects."
                )
            resolve_citation(source, artifacts)


def resolve_citation(
    source: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> tuple[dict[str, Any], str, Any]:
    artifact_id = source.get("artifact_id")
    schema = source.get("schema_version")
    if artifact_id is not None and not isinstance(artifact_id, str):
        raise ReportError("Analyst-note citation artifact_id must be a string.")
    if schema is not None and not isinstance(schema, str):
        raise ReportError("Analyst-note citation schema_version must be a string.")
    label = source.get("label")
    if label is not None and not isinstance(label, str):
        raise ReportError("Analyst-note citation label must be a string.")
    candidates = artifacts
    if artifact_id:
        candidates = [
            artifact
            for artifact in artifacts
            if artifact.get("artifact_id") == artifact_id
        ]
        if not candidates:
            raise ReportError(
                f"Analyst-note citation artifact_id {artifact_id} cannot be resolved."
            )
        artifact = candidates[0]
        if schema and artifact.get("schema_version") != schema:
            raise ReportError(
                f"Analyst-note citation {artifact_id} schema mismatch: expected {schema}."
            )
    elif schema:
        candidates = [
            artifact
            for artifact in artifacts
            if artifact.get("schema_version") == schema
        ]
        if len(candidates) != 1:
            raise ReportError(
                f"Analyst-note schema-only citation {schema} is ambiguous or missing."
            )
        artifact = candidates[0]
    else:
        raise ReportError(
            "Analyst-note citation requires artifact_id or schema_version."
        )

    pointer = source.get("json_pointer")
    if not isinstance(pointer, str):
        raise ReportError("Analyst-note citation is missing json_pointer.")
    try:
        normalized_pointer, resolved = json_pointer_resolve(artifact, pointer)
        return artifact, normalized_pointer, resolved
    except (KeyError, IndexError, ValueError, TypeError):
        raise ReportError(
            f"Analyst-note citation pointer {pointer} cannot be resolved."
        )


def md_analyst_notes(
    notes: list[dict[str, Any]], artifacts: list[dict[str, Any]], ctx: ReportContext
) -> str:
    if not notes:
        return ""
    parts = [
        "## Analyst Notes",
        "These notes are interpretive narrative, not facts strictly proven by artifact data alone.",
    ]
    for index, note in enumerate(notes, start=1):
        author = note.get("author_type")
        if author not in {"llm", "analyst"}:
            ctx.warn(
                f"Analyst note {note.get('note_id', index)} has unsupported author_type {author}."
            )
            author = "analyst"
        label = "LLM interpretation" if author == "llm" else "Analyst interpretation"
        title = note.get("title") or f"Note {index}"
        parts.append(
            f"### {md_escape(title)}\n\n_{label}._ {md_escape(note.get('text', ''))}"
        )
        if note.get("show_data_sources") is False:
            continue
        sources = note.get("data_sources")
        if not isinstance(sources, list) or not sources:
            ctx.warn(
                f"Analyst note {note.get('note_id', index)} has no cited data sources."
            )
            continue
        citations = []
        for source in sources:
            _artifact, normalized_pointer, resolved = resolve_citation(
                source, artifacts
            )
            label = source.get("label") or "Supporting value"
            percent = normalized_pointer.endswith("/pct_change")
            citations.append(
                f"- {md_escape(label)}: {md_escape(human_number(resolved, percent=percent))}"
            )
        if citations:
            parts.append("Supporting evidence:\n\n" + "\n".join(citations))
    return "\n\n".join(parts)


def _chart_numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _chart_skip(heading: str, reason: str, ctx: ReportContext) -> str:
    ctx.warn(f"Chart '{heading}' skipped because {reason}.")
    return (
        f'<div class="chart-skip" role="note" aria-label="{h_escape(heading)} skipped">'
        f"<strong>{h_escape(heading)}</strong>: chart skipped because "
        f"{h_escape(reason)}.</div>"
    )


def _chart_open(heading: str, width: int, height: int) -> str:
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img"'
        f' aria-label="{h_escape(heading)}">'
        f"<title>{h_escape(heading)}</title>"
        f'<text class="chart-title" x="0" y="16">{h_escape(heading)}</text>'
    )


def _horizontal_bars_svg(
    heading: str,
    rows: list[tuple[str, float, str]],
    *,
    width: int = 720,
    label_width: int = 220,
    row_height: int = 30,
) -> str:
    max_value = max((abs(value) for _, value, _ in rows), default=0.0) or 1.0
    bar_max = width - label_width - 120
    height = 40 + len(rows) * row_height
    parts = [_chart_open(heading, width, height)]
    for index, (label, value, display) in enumerate(rows):
        y = 32 + index * row_height
        scaled = max(1, int(abs(value) / max_value * bar_max))
        parts.append(
            f'<text class="chart-label" x="0" y="{y + 14}">{h_escape(label)}</text>'
        )
        parts.append(
            f'<rect class="chart-bar" x="{label_width}" y="{y}"'
            f' width="{scaled}" height="18" rx="2"></rect>'
        )
        parts.append(
            f'<text class="chart-value" x="{label_width + scaled + 8}"'
            f' y="{y + 14}">{h_escape(display)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _gauge_arc_path(
    cx: float,
    cy: float,
    radius: float,
    start_degrees: float,
    end_degrees: float,
) -> str:
    start = math.radians(start_degrees)
    end = math.radians(end_degrees)
    start_x = cx + radius * math.cos(start)
    start_y = cy + radius * math.sin(start)
    end_x = cx + radius * math.cos(end)
    end_y = cy + radius * math.sin(end)
    large_arc = 1 if abs(end_degrees - start_degrees) > 180 else 0
    sweep = 1 if end_degrees > start_degrees else 0
    return (
        f"M {start_x:.1f} {start_y:.1f} "
        f"A {radius:.1f} {radius:.1f} 0 {large_arc} {sweep} {end_x:.1f} {end_y:.1f}"
    )


def html_metric_delta_cards(
    posture: dict[str, Any], limit: int, ctx: ReportContext
) -> str:
    heading = "Metric Delta Cards"
    metrics = posture.get("metrics") or []
    if not isinstance(metrics, list) or not metrics:
        return _chart_skip(heading, "no metrics available", ctx)
    metrics = metrics[:limit] if limit else metrics
    card_w, card_h, gap = 240, 140, 14
    cols = 3
    rows_count = (len(metrics) + cols - 1) // cols
    width = cols * card_w + (cols + 1) * gap
    height = 48 + rows_count * (card_h + gap)
    parts = [_chart_open(heading, width, height)]
    for idx, metric in enumerate(metrics):
        col = idx % cols
        row = idx // cols
        x = gap + col * (card_w + gap)
        y = 40 + row * (card_h + gap)
        parts.append(
            f'<rect class="chart-card" x="{x}" y="{y}"'
            f' width="{card_w}" height="{card_h}" rx="4"></rect>'
        )
        parts.append(
            f'<text class="chart-label" x="{x + 12}" y="{y + 22}">'
            f"{h_escape(human_metric_name(metric.get('name')))}</text>"
        )
        parts.append(
            f'<text class="chart-value" x="{x + 12}" y="{y + 46}">'
            f"Current: {h_escape(human_number(metric.get('current')))}</text>"
        )
        parts.append(
            f'<text class="chart-value" x="{x + 12}" y="{y + 64}">'
            f"Baseline: {h_escape(human_number(metric.get('baseline')))}</text>"
        )
        parts.append(
            f'<text class="chart-value" x="{x + 12}" y="{y + 82}">'
            f"Delta: {h_escape(human_delta(metric.get('absolute_delta')))}</text>"
        )
        parts.append(
            f'<text class="chart-value" x="{x + 12}" y="{y + 100}">'
            f"Change: {h_escape(human_number(metric.get('pct_change'), percent=True))}</text>"
        )
        parts.append(
            f'<text class="chart-value" x="{x + 12}" y="{y + 120}">'
            f"{h_escape(metric.get('direction'))} /"
            f" {h_escape(metric.get('confidence'))}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def html_current_baseline_bars(
    posture: dict[str, Any], limit: int, ctx: ReportContext
) -> str:
    heading = "Current Versus Baseline Bars"
    metrics = posture.get("metrics") or []
    usable: list[tuple[dict[str, Any], float | None, float | None]] = []
    for metric in metrics:
        current = _chart_numeric(metric.get("current"))
        baseline = _chart_numeric(metric.get("baseline"))
        if current is None and baseline is None:
            continue
        usable.append((metric, current, baseline))
    if not usable:
        return _chart_skip(
            heading, "no numeric current or baseline values available", ctx
        )
    usable = usable[:limit] if limit else usable
    width = 720
    label_w = 180
    row_height = 72
    bar_max = width - label_w - 140
    height = 40 + row_height * len(usable)
    parts = [_chart_open(heading, width, height)]
    for idx, (metric, current, baseline) in enumerate(usable):
        y = 32 + idx * row_height
        parts.append(
            f'<text class="chart-label" x="0" y="{y + 14}">'
            f"{h_escape(human_metric_name(metric.get('name')))}</text>"
        )
        local_max = max(abs(current or 0.0), abs(baseline or 0.0)) or 1.0
        cur_w = (
            max(1, int(abs(current) / local_max * bar_max))
            if current is not None
            else 0
        )
        base_w = (
            max(1, int(abs(baseline) / local_max * bar_max))
            if baseline is not None
            else 0
        )
        parts.append(
            f'<rect class="chart-bar chart-current" x="{label_w}" y="{y}"'
            f' width="{cur_w}" height="16" rx="2"></rect>'
        )
        parts.append(
            f'<text class="chart-value" x="{label_w + cur_w + 8}" y="{y + 13}">'
            f"current {h_escape(human_number(metric.get('current')))}</text>"
        )
        parts.append(
            f'<rect class="chart-bar chart-baseline" x="{label_w}" y="{y + 22}"'
            f' width="{base_w}" height="16" rx="2"></rect>'
        )
        parts.append(
            f'<text class="chart-value" x="{label_w + base_w + 8}" y="{y + 35}">'
            f"baseline {h_escape(human_number(metric.get('baseline')))}</text>"
        )
        parts.append(
            f'<text class="chart-value" x="{label_w}" y="{y + 57}">'
            f"direction {h_escape(metric.get('direction'))} · "
            f"confidence {h_escape(metric.get('confidence'))}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def html_ranking_bars(index: dict[str, Any], limit: int, ctx: ReportContext) -> str:
    heading = "Scorecard Ranking Bars"
    ranked = index.get("ranked_entities") or []
    rows: list[tuple[str, float, str]] = []
    for entity in ranked:
        score = _chart_numeric(entity.get("score"))
        if score is None:
            continue
        rank = entity.get("rank")
        rank_prefix = f"Rank {stringify(rank)}: " if rank is not None else ""
        label = (
            f"{rank_prefix}{stringify(entity.get('entity'))}"
            f" ({stringify(entity.get('entity_type'))})"
        )
        display = (
            f"score {stringify(entity.get('score'))}"
            f" · band {stringify(entity.get('band'))}"
            f" · primary {stringify(entity.get('primary_domain'))}"
            f" · confidence {stringify(entity.get('confidence'))}"
        )
        rows.append((label, score, display))
    if not rows:
        return _chart_skip(heading, "no numeric ranked scores available", ctx)
    rows = rows[:limit] if limit else rows
    return _horizontal_bars_svg(heading, rows)


def html_scorecard_score_bars(
    scorecards: list[dict[str, Any]], limit: int, ctx: ReportContext
) -> str:
    heading = "Scorecard Ranking Bars"
    rows: list[tuple[str, float, str]] = []
    for card in scorecards:
        score = _chart_numeric(card.get("score"))
        if score is None:
            continue
        label = (
            f"{stringify(card.get('entity'))} ({stringify(card.get('entity_type'))})"
        )
        display = (
            f"sorted by lower health score · score {stringify(card.get('score'))}"
            f" · band {stringify(card.get('band'))}"
            f" · primary {stringify(card.get('primary_domain'))}"
            f" · confidence {stringify(card.get('confidence'))}"
        )
        rows.append((label, score, display))
    if not rows:
        return _chart_skip(heading, "no numeric scorecard scores available", ctx)
    rows.sort(key=lambda item: (item[1], item[0]))
    rows = rows[:limit] if limit else rows
    return _horizontal_bars_svg(heading, rows)


def html_scorecard_overall_gauge(card: dict[str, Any], ctx: ReportContext) -> str:
    score = _chart_numeric(card.get("score"))
    if score is None:
        return ""
    baseline = _chart_numeric(card.get("baseline_score"))
    delta = _chart_numeric(card.get("score_delta_points"))
    if delta is None and baseline is not None:
        delta = score - baseline
    fill_pct = min(100.0, max(0.0, score))
    start_degrees = 150.0
    sweep_degrees = 240.0
    end_degrees = start_degrees + sweep_degrees
    fill_degrees = start_degrees + sweep_degrees * (fill_pct / 100.0)
    track = _gauge_arc_path(100, 96, 78, start_degrees, end_degrees)
    fill = (
        _gauge_arc_path(100, 96, 78, start_degrees, fill_degrees)
        if fill_pct > 0
        else ""
    )
    fill_path = (
        f'<path class="overall-gauge-fill" d="{h_escape(fill)}"></path>' if fill else ""
    )
    if delta is None:
        delta_text = "Delta unavailable vs baseline"
        delta_class = "score-delta-neutral"
    else:
        delta_class = (
            "score-delta-up"
            if delta > 0
            else "score-delta-down"
            if delta < 0
            else "score-delta-neutral"
        )
        if delta == 0:
            delta_text = "No Change"
        else:
            sign = "+" if delta > 0 else "-"
            delta_text = f"{sign}{human_number(abs(delta))} pts vs baseline"
    score_text = human_number(card.get("score"))
    return (
        '<section class="score-hero" aria-label="Overall Score">'
        '<div class="score-hero-label">Overall Score</div>'
        '<svg class="overall-gauge" viewBox="0 0 200 150" role="img" aria-label="Overall Score Gauge">'
        f'<path class="overall-gauge-track" d="{h_escape(track)}"></path>'
        f"{fill_path}"
        f'<text class="overall-gauge-metric" x="100" y="96" text-anchor="middle">{h_escape(score_text)}</text>'
        "</svg>"
        f'<div class="{delta_class}">{h_escape(delta_text)}</div>'
        "</section>"
    )


def html_scorecard_context_panel(selected: dict[str, Any]) -> str:
    card = selected["scorecard"]
    index = selected.get("index") or {}
    rank = None
    total_ranked = index.get("total_ranked_entities") or index.get("result_row_count")
    for row in index.get("ranked_entities", []):
        if (
            isinstance(row, dict)
            and row.get("entity_type") == card.get("entity_type")
            and row.get("entity") == card.get("entity")
        ):
            rank = row.get("rank")
            break
    if rank is not None and total_ranked:
        rank_display = f"{human_number(rank)} of {human_number(total_ranked)}"
    elif rank is not None:
        rank_display = human_number(rank)
    else:
        rank_display = "Unavailable"
    entity_type = display_label(card.get("entity_type"))
    metadata_items = [
        ("Rank", rank_display),
        ("Current Score", human_number(card.get("score"))),
        ("Baseline Score", human_number(card.get("baseline_score"))),
        ("Primary Domain", display_label(card.get("primary_domain"))),
        ("Confidence", stringify(card.get("confidence"))),
    ]
    metadata_html = "".join(
        '<span class="entity-metadata-item">'
        f'<span class="entity-metadata-label">{h_escape(label)}</span>'
        f'<span class="entity-metadata-value">{h_escape(value)}</span>'
        "</span>"
        for label, value in metadata_items
    )
    return (
        "<h2>Selected Entity Context</h2>"
        '<section class="entity-identity" aria-label="Selected Entity Context">'
        f'<div class="entity-dimension">{h_escape(entity_type)}</div>'
        f'<div class="entity-name">{h_escape(stringify(card.get("entity")))}</div>'
        "<p>This brief explains the selected entity from the larger scored entity set.</p>"
        f'<div class="entity-metadata-row">{metadata_html}</div>'
        "</section>"
    )


CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


def scorecard_triggered_rules(card: dict[str, Any]) -> list[dict[str, Any]]:
    rules = [
        rule
        for rule in card.get("rule_results", [])
        if isinstance(rule, dict) and rule.get("status") == "triggered"
    ]
    if rules:
        return rules
    return [
        feature
        for feature in card.get("features", [])
        if isinstance(feature, dict) and (to_float(feature.get("points")) or 0) > 0
    ]


def scorecard_has_trigger(card: dict[str, Any]) -> bool:
    return bool(scorecard_triggered_rules(card))


def lowest_confidence(cards: list[dict[str, Any]]) -> str:
    values = [stringify(card.get("confidence")).lower() for card in cards]
    known_values = [value for value in values if value in CONFIDENCE_ORDER]
    if not known_values:
        return "unavailable"
    return min(known_values, key=lambda value: CONFIDENCE_ORDER[value])


def scorecard_rank_lookup(
    index: dict[str, Any] | None,
) -> dict[tuple[str, str], dict[str, Any]]:
    if not isinstance(index, dict):
        return {}
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for row in index.get("ranked_entities", []):
        if isinstance(row, dict):
            lookup[
                (stringify(row.get("entity_type")), stringify(row.get("entity")))
            ] = row
    return lookup


def fleet_ordered_scorecards(
    cards: list[dict[str, Any]], index: dict[str, Any] | None
) -> list[tuple[int | None, dict[str, Any], dict[str, Any] | None]]:
    ranks = scorecard_rank_lookup(index)
    rows: list[tuple[int | None, int, dict[str, Any], dict[str, Any] | None]] = []
    for position, card in enumerate(cards):
        row = ranks.get(
            (stringify(card.get("entity_type")), stringify(card.get("entity")))
        )
        rank_number = to_float(row.get("rank")) if row else None
        rank = int(rank_number) if rank_number is not None else None
        rows.append((rank, position, card, row))
    rows.sort(key=lambda item: (item[0] is None, item[0] or item[1] + 1, item[1]))
    return [(rank, card, row) for rank, _position, card, row in rows]


def scorecard_primary_evidence(card: dict[str, Any]) -> str:
    summary = card.get("evidence_summary")
    if isinstance(summary, list):
        for item in summary:
            if item not in (None, ""):
                return stringify(item)
    for rule in scorecard_triggered_rules(card):
        evidence = rule.get("evidence")
        if evidence not in (None, ""):
            return stringify(evidence)
    return "No concise evidence emitted."


def fleet_rule_coverage(cards: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    coverage: dict[str, dict[str, int]] = {}
    for card in cards:
        for rule in card.get("rule_results", []):
            if not isinstance(rule, dict):
                continue
            status = rule.get("status")
            if status not in {"triggered", "evaluated_zero", "missing_input"}:
                continue
            domain = stringify(rule.get("domain"))
            bucket = coverage.setdefault(
                domain,
                {"triggered": 0, "evaluated_zero": 0, "missing_input": 0},
            )
            bucket[status] += 1
    return coverage


def fleet_common_triggered_feature(cards: list[dict[str, Any]]) -> tuple[str, int]:
    counts: dict[str, int] = {}
    for card in cards:
        seen_for_card: set[str] = set()
        for rule in scorecard_triggered_rules(card):
            name = stringify(rule.get("name"))
            if name in seen_for_card:
                continue
            seen_for_card.add(name)
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return "No triggered feature emitted", 0
    name, count = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
    return " ".join(part for part in rule_label_parts(name) if part), count


def fleet_health_score(cards: list[dict[str, Any]]) -> float | None:
    scores = [
        score for card in cards if (score := to_float(card.get("score"))) is not None
    ]
    if not scores:
        return None
    return sum(scores) / len(scores)


def html_fleet_kpis(cards: list[dict[str, Any]], index: dict[str, Any] | None) -> str:
    entity_count = (
        index.get("total_ranked_entities")
        if isinstance(index, dict) and index.get("total_ranked_entities") is not None
        else len(cards)
    )
    triggered_count = sum(1 for card in cards if scorecard_has_trigger(card))
    movement_count = sum(
        1 for card in cards if (to_float(card.get("score_delta_points")) or 0) != 0
    )
    confidence = lowest_confidence(cards)
    health_score = fleet_health_score(cards)
    kpis = [
        (
            "Fleet Health Score",
            human_number(health_score) if health_score is not None else "unavailable",
        ),
        ("Entities Evaluated", human_number(entity_count)),
        ("Entities With Triggered Rules", human_number(triggered_count)),
        ("Score Movement Count", human_number(movement_count)),
        ("Confidence Ceiling", confidence),
    ]
    return (
        '<section class="fleet-kpis" aria-label="Fleet KPI Strip">'
        + "".join(
            '<div class="fleet-kpi">'
            f'<div class="fleet-kpi-label">{h_escape(label)}</div>'
            f'<div class="fleet-kpi-value">{h_escape(value)}</div>'
            "</div>"
            for label, value in kpis
        )
        + "</section>"
    )


def html_fleet_findings(cards: list[dict[str, Any]]) -> str:
    triggered_count = sum(1 for card in cards if scorecard_has_trigger(card))
    feature_label, feature_count = fleet_common_triggered_feature(cards)
    coverage = fleet_rule_coverage(cards)
    missing_total = sum(bucket["missing_input"] for bucket in coverage.values())
    movement_count = sum(
        1 for card in cards if (to_float(card.get("score_delta_points")) or 0) != 0
    )
    findings = [
        f"{human_number(triggered_count)} of {human_number(len(cards))} entities have triggered scorecard rules or positive scored features.",
        (
            f"Most common triggered feature: {feature_label} "
            f"across {human_number(feature_count)} entities."
            if feature_count
            else "No triggered feature was emitted by the scorecards."
        ),
        f"Missing-input coverage: {human_number(missing_total)} rule evaluations were unavailable across {human_number(len(coverage))} domains.",
        f"Score movement count: {human_number(movement_count)} entities have nonzero score_delta_points.",
    ]
    return (
        '<section class="fleet-findings" aria-label="What this report says">'
        "<h2>What This Report Says</h2><ul>"
        + "".join(f"<li>{h_escape(finding)}</li>" for finding in findings)
        + "</ul></section>"
    )


def html_fleet_coverage(cards: list[dict[str, Any]]) -> str:
    coverage = fleet_rule_coverage(cards)
    if not coverage:
        return "<section><h2>Rule Coverage By Domain</h2><p>No rule_results coverage emitted.</p></section>"
    rows = []
    for domain, counts in sorted(coverage.items()):
        total = sum(counts.values()) or 1
        bars = "".join(
            f'<span class="coverage-segment coverage-{h_escape(status.replace("_", "-"))}" '
            f'style="width:{counts[status] / total * 100:.1f}%"></span>'
            for status in ("triggered", "evaluated_zero", "missing_input")
            if counts[status]
        )
        rows.append(
            "<tr>"
            f"<td>{h_escape(display_label(domain))}</td>"
            f"<td>{h_escape(human_number(counts['triggered']))}</td>"
            f"<td>{h_escape(human_number(counts['evaluated_zero']))}</td>"
            f"<td>{h_escape(human_number(counts['missing_input']))}</td>"
            f'<td><div class="coverage-bar">{bars}</div></td>'
            "</tr>"
        )
    return (
        '<section class="fleet-coverage"><h2>Rule Coverage By Domain</h2>'
        "<table><thead><tr><th>Domain</th><th>Triggered</th><th>Evaluated Zero</th>"
        "<th>Missing Input</th><th>Coverage</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></section>"
    )


def html_fleet_ranked_entities(
    cards: list[dict[str, Any]], index: dict[str, Any] | None, limit: int
) -> str:
    ordered = fleet_ordered_scorecards(cards, index)
    if limit > 0:
        ordered = ordered[:limit]
    rows = []
    for fallback_position, (rank, card, row) in enumerate(ordered, start=1):
        effective_rank = rank if rank is not None else fallback_position
        score = (
            row.get("score")
            if row and row.get("score") is not None
            else card.get("score")
        )
        primary = (
            row.get("primary_domain")
            if row and row.get("primary_domain") is not None
            else card.get("primary_domain")
        )
        confidence = (
            row.get("confidence")
            if row and row.get("confidence") is not None
            else card.get("confidence")
        )
        rows.append(
            "<tr>"
            f"<td>{h_escape(human_number(effective_rank))}</td>"
            f"<td>{h_escape(stringify(card.get('entity')))}</td>"
            f"<td>{h_escape(human_number(score))}</td>"
            f"<td>{h_escape(human_delta(card.get('score_delta_points')))}</td>"
            f"<td>{h_escape(display_label(primary))}</td>"
            f"<td>{h_escape(confidence)}</td>"
            f"<td>{h_escape(scorecard_primary_evidence(card))}</td>"
            "</tr>"
        )
    return (
        '<section class="fleet-ranking"><h2>Ranked Entities</h2>'
        "<table><thead><tr><th>Rank</th><th>Entity</th><th>Score</th>"
        "<th>Score Delta</th><th>Primary Domain</th><th>Confidence</th>"
        "<th>Concise Evidence</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></section>"
    )


def html_fleet_next_steps(cards: list[dict[str, Any]]) -> str:
    groups: dict[str, list[str]] = {}
    for card in cards:
        for step in card.get("recommended_next_steps") or []:
            if step in (None, ""):
                continue
            text = step["detail"] if isinstance(step, dict) else step
            if not text:
                continue
            groups.setdefault(stringify(text), []).append(stringify(card.get("entity")))
    if not groups:
        return "<section><h2>Recommended Next Steps</h2><p>No recommended next steps emitted.</p></section>"
    rows = [
        "<tr>"
        f"<td>{h_escape(step)}</td>"
        f"<td>{h_escape(human_number(len(entities)))}</td>"
        f"<td>{h_escape(', '.join(entities[:8]))}</td>"
        "</tr>"
        for step, entities in sorted(
            groups.items(), key=lambda item: (-len(item[1]), item[0])
        )
    ]
    return (
        '<section class="fleet-next-steps"><h2>Recommended Next Steps</h2>'
        "<table><thead><tr><th>Action</th><th>Affected Entities</th><th>Entities</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></section>"
    )


def html_fleet_method(
    cards: list[dict[str, Any]], index: dict[str, Any] | None, scope_text: str
) -> str:
    reference = index or cards[0]
    confidence_reasons = sorted(
        {
            stringify(reason)
            for card in cards
            for reason in (card.get("confidence_reasons") or [])
            if reason not in (None, "")
        }
    )
    constraints = sorted(
        {
            stringify(item)
            for artifact in ([index] if index else []) + cards
            if isinstance(artifact, dict)
            for item in (artifact.get("interpretation_constraints") or [])
            if item not in (None, "")
        }
    )
    producer = (
        _producer_limit_bullet(index or {})
        or _producer_limit_bullet(cards[0])
        or "unavailable"
    )
    rows = [
        ["Scope", scope_text],
        ["Current Window", compact_window_range(reference.get("current_window"))],
        [
            "Baseline Window",
            compact_window_range((reference.get("baseline_windows") or [{}])[0])
            if isinstance(reference.get("baseline_windows"), list)
            else "unavailable",
        ],
        ["Table", reference.get("table_used")],
        [
            "Confidence Reasons",
            ", ".join(confidence_reasons) if confidence_reasons else "unavailable",
        ],
        ["Producer Limits", producer],
        [
            "Interpretation Constraints",
            ", ".join(constraints) if constraints else "unavailable",
        ],
    ]
    return (
        '<section class="fleet-method"><h2>Method And Caveats</h2>'
        "<table><thead><tr><th>Field</th><th>Value</th></tr></thead><tbody>"
        + "".join(
            f"<tr><td>{h_escape(label)}</td><td>{h_escape(value)}</td></tr>"
            for label, value in rows
        )
        + "</tbody></table></section>"
    )


def html_scorecard_fleet_report(
    title: str,
    selected: dict[str, Any],
    all_artifacts: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    limit: int,
    ctx: ReportContext,
    scope_label: str | None,
) -> str:
    cards = selected.get("scorecards") or [selected["scorecard"]]
    index = selected.get("index")
    reference = index or cards[0]
    scope_text = resolve_scope_display(scope_label, selected, ctx)
    entity_type = (
        (reference.get("scope") or {}).get("entity_type")
        or cards[0].get("entity_type")
        or "entity"
    )
    header_items = [
        ("Scope", scope_text),
        ("Entity Type", display_label(entity_type)),
        ("Current Window", compact_window_range(reference.get("current_window"))),
        (
            "Baseline Window",
            compact_window_range((reference.get("baseline_windows") or [{}])[0])
            if isinstance(reference.get("baseline_windows"), list)
            else "unavailable",
        ),
    ]
    header = (
        f"<h1>{h_escape(title)}</h1>"
        '<section class="fleet-header" aria-label="Report Header">'
        + "".join(
            '<span class="entity-metadata-item">'
            f'<span class="entity-metadata-label">{h_escape(label)}</span>'
            f'<span class="entity-metadata-value">{h_escape(value)}</span>'
            "</span>"
            for label, value in header_items
        )
        + "</section>"
    )
    notes_html = (
        markdown_to_simple_html(md_analyst_notes(notes, all_artifacts, ctx))
        if notes
        else ""
    )
    return (
        header
        + html_fleet_kpis(cards, index)
        + html_fleet_findings(cards)
        + notes_html
        + html_fleet_coverage(cards)
        + html_fleet_ranked_entities(cards, index, limit)
        + html_fleet_next_steps(cards)
        + html_fleet_method(cards, index, scope_text)
    )


def html_mover_bars(mover: dict[str, Any], limit: int, ctx: ReportContext) -> str:
    heading = "Mover Contribution Bars"
    movers = mover.get("movers") or []
    use_contribution = any(
        _chart_numeric(row.get("contribution_pct")) is not None for row in movers
    )
    rows: list[tuple[int, str, float, str]] = []
    total_delta = mover.get("total_delta")
    total_delta_text = (
        f" · total delta {stringify(total_delta)}" if total_delta is not None else ""
    )
    for index, row in enumerate(movers):
        if use_contribution:
            value = _chart_numeric(row.get("contribution_pct"))
            display = (
                f"contribution {stringify(row.get('contribution_pct'))}"
                f" · confidence {stringify(row.get('confidence'))}"
                f"{total_delta_text}"
            )
        else:
            value = _chart_numeric(row.get("absolute_delta"))
            display = (
                f"delta {stringify(row.get('absolute_delta'))}"
                f" · confidence {stringify(row.get('confidence'))}"
                f"{total_delta_text}"
            )
        if value is None:
            continue
        label = f"{stringify(row.get('value'))} ({stringify(row.get('metric'))})"
        rows.append((index, label, value, display))
    if not rows:
        return _chart_skip(
            heading,
            "no numeric contribution_pct or absolute_delta values available",
            ctx,
        )
    rows.sort(key=lambda item: (-abs(item[2]), item[0]))
    limited = rows[:limit] if limit else rows
    return _horizontal_bars_svg(
        heading, [(label, value, display) for _, label, value, display in limited]
    )


def html_scorecard_domain_bars(card: dict[str, Any], ctx: ReportContext) -> str:
    heading = "Domain Scores"
    domain_scores = card.get("domain_scores") or {}
    rows: list[tuple[str, float, str]] = []
    for domain in sorted(domain_scores):
        value = _chart_numeric(domain_scores[domain])
        if value is None:
            continue
        rows.append(
            (
                stringify(domain),
                value,
                f"score {stringify(domain_scores[domain])}",
            )
        )
    if not rows:
        return _chart_skip(heading, "no numeric domain scores available", ctx)
    return _horizontal_bars_svg(heading, rows)


def scorecard_rule_results(card: dict[str, Any]) -> list[dict[str, Any]]:
    rule_results = [
        rule
        for rule in card.get("rule_results", [])
        if isinstance(rule, dict)
        and rule.get("status") in {"triggered", "evaluated_zero"}
    ]
    if rule_results:
        return rule_results
    fallback: list[dict[str, Any]] = []
    for feature in card.get("features", []):
        if isinstance(feature, dict):
            result = dict(feature)
            result["status"] = "triggered"
            fallback.append(result)
    return fallback


def html_scorecard_feature_cards(
    card: dict[str, Any], limit: int, ctx: ReportContext
) -> str:
    heading = "Rule Score Matrix"
    rules = scorecard_rule_results(card)
    if not rules:
        return _chart_skip(heading, "no evaluated scorecard rules available", ctx)
    rules = sorted(
        rules,
        key=lambda rule: (str(rule.get("domain")), str(rule.get("name"))),
    )

    def is_percent_rule(rule: dict[str, Any]) -> bool:
        text = str(rule.get("name", ""))
        return any(token in text for token in ("pct", "rate", "share", "miss"))

    def metric_text(rule: dict[str, Any], value: float | None) -> str:
        if value is None:
            return "N/A"
        if is_percent_rule(rule):
            return f"{value:.2f}%"
        return human_number(value)

    def delta_value(rule: dict[str, Any]) -> float | None:
        supporting = rule.get("supporting_metrics")
        if isinstance(supporting, dict):
            for key in (
                "absolute_delta_points",
                "pct_change",
                "absolute_delta",
                "absolute_delta_ms",
            ):
                value = _chart_numeric(supporting.get(key))
                if value is not None:
                    return value
        current = _chart_numeric(rule.get("current"))
        baseline = _chart_numeric(rule.get("baseline"))
        if current is not None and baseline is not None:
            return current - baseline
        return None

    def gauge_value(rule: dict[str, Any], current: float | None) -> float | None:
        if "delta" in stringify(rule.get("name")):
            delta = delta_value(rule)
            if delta is not None:
                return abs(delta)
        return current

    def card_label(value: Any) -> str:
        return display_label(value)

    def points_badge_text(value: Any) -> str:
        points = _chart_numeric(value)
        if points is None:
            return stringify(value)
        if points > 0:
            return f"-{human_number(points)}"
        return human_number(points)

    def delta_text(rule: dict[str, Any]) -> tuple[str, str]:
        if rule.get("status") == "missing_input":
            return "Missing inputs", "rule-delta-neutral"
        delta = delta_value(rule)
        if delta is None:
            return "delta unavailable", "rule-delta-neutral"
        symbol = "^" if delta > 0 else "v" if delta < 0 else "-"
        css_class = (
            "rule-delta-up"
            if delta > 0
            else "rule-delta-down"
            if delta < 0
            else "rule-delta-neutral"
        )
        display = (
            f"{abs(delta):.2f}%" if is_percent_rule(rule) else human_number(abs(delta))
        )
        return f"{symbol} {display}", css_class

    def gauge_html(rule: dict[str, Any], current: float | None, value: str) -> str:
        if current is None:
            return ""
        threshold = _chart_numeric(rule.get("threshold"))
        baseline = _chart_numeric(rule.get("baseline"))
        if is_percent_rule(rule):
            max_value = 100.0
        elif threshold is not None and threshold > 0:
            max_value = threshold * 1.25
        elif baseline is not None and abs(baseline) > 0:
            max_value = max(abs(current), abs(baseline))
        else:
            max_value = abs(current) if abs(current) > 0 else 1.0
        fill_pct = min(100.0, max(0.0, abs(current) / max_value * 100.0))
        start_degrees = 150.0
        sweep_degrees = 240.0
        end_degrees = start_degrees + sweep_degrees
        fill_degrees = start_degrees + sweep_degrees * (fill_pct / 100.0)
        track = _gauge_arc_path(60, 58, 44, start_degrees, end_degrees)
        fill = (
            _gauge_arc_path(60, 58, 44, start_degrees, fill_degrees)
            if fill_pct > 0
            else ""
        )
        fill_path = (
            f'<path class="gauge-fill" d="{h_escape(fill)}"></path>' if fill else ""
        )
        return (
            '<svg class="rule-gauge" viewBox="0 0 120 90" aria-hidden="true" focusable="false">'
            f'<path class="gauge-track" d="{h_escape(track)}"></path>'
            f"{fill_path}"
            f'<text class="gauge-metric" x="60" y="57" text-anchor="middle">{h_escape(value)}</text>'
            "</svg>"
        )

    cards: list[str] = []
    for rule in rules:
        domain = card_label(rule.get("domain"))
        name, condition = rule_label_parts(rule.get("name"))
        status = stringify(rule.get("status")).replace("_", " ")
        current = _chart_numeric(rule.get("current"))
        gauge_current = gauge_value(rule, current)
        value = metric_text(rule, gauge_current)
        delta, delta_class = delta_text(rule)
        points = points_badge_text(rule.get("points") or 0)
        gauge = gauge_html(rule, gauge_current, value)
        status_class = (
            "rule-status rule-status-triggered"
            if rule.get("status") == "triggered"
            else "rule-status"
        )
        cards.append(
            '<div class="rule-card">'
            f'<div class="rule-card-top"><span class="rule-domain">{h_escape(domain)}</span>'
            f'<span class="rule-points">{h_escape(points)} pts</span></div>'
            f'<div class="rule-name">{h_escape(name)}</div>'
            f'<div class="rule-condition">{h_escape(condition)}</div>'
            f"{gauge}"
            f'<div class="{delta_class}">{h_escape(delta)}</div>'
            f'<div class="{status_class}">{h_escape(status)}</div>'
            "</div>"
        )
    return (
        f'<section class="rule-matrix" aria-label="{h_escape(heading)}">'
        f"<h2>{h_escape(heading)}</h2>"
        '<div class="rule-grid">' + "".join(cards) + "</div></section>"
    )


def html_domain_matrix(
    scorecards: list[dict[str, Any]], limit: int, ctx: ReportContext
) -> str:
    heading = "Domain Score Matrix"
    if not scorecards:
        return _chart_skip(heading, "no scorecards available", ctx)
    cards = scorecards[:limit] if limit else scorecards
    domain_order: list[str] = []
    seen: set[str] = set()
    for card in cards:
        for domain in (card.get("domain_scores") or {}).keys():
            if domain not in seen:
                seen.add(domain)
                domain_order.append(domain)
    if not domain_order:
        return _chart_skip(heading, "no domain scores on scorecards", ctx)
    label_w = 220
    cell_w = 110
    row_h = 36
    width = label_w + len(domain_order) * cell_w + 20
    height = 72 + len(cards) * row_h
    max_score = 1.0
    for card in cards:
        for domain in domain_order:
            value = _chart_numeric((card.get("domain_scores") or {}).get(domain))
            if value is not None and abs(value) > max_score:
                max_score = abs(value)
    parts = [_chart_open(heading, width, height)]
    for idx, domain in enumerate(domain_order):
        x = label_w + idx * cell_w + cell_w // 2
        parts.append(
            f'<text class="chart-label" x="{x}" y="50" text-anchor="middle">'
            f"{h_escape(domain)}</text>"
        )
    for row_idx, card in enumerate(cards):
        y = 64 + row_idx * row_h
        parts.append(
            f'<text class="chart-label" x="0" y="{y + 22}">'
            f"{h_escape(card.get('entity'))}</text>"
        )
        domain_scores = card.get("domain_scores") or {}
        for idx, domain in enumerate(domain_order):
            x = label_w + idx * cell_w
            value = _chart_numeric(domain_scores.get(domain))
            intensity = (
                0.15 if value is None else min(1.0, max(0.15, abs(value) / max_score))
            )
            parts.append(
                f'<rect class="chart-cell" x="{x + 4}" y="{y + 4}"'
                f' width="{cell_w - 8}" height="{row_h - 8}" rx="2"'
                f' fill-opacity="{intensity:.2f}"></rect>'
            )
            display = (
                "unavailable" if value is None else stringify(domain_scores.get(domain))
            )
            parts.append(
                f'<text class="chart-value" x="{x + cell_w // 2}" y="{y + 24}"'
                f' text-anchor="middle">{h_escape(display)}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def html_control_bars(control: dict[str, Any], limit: int, ctx: ReportContext) -> str:
    heading = "Control Before/After/Expected Bars"
    effects = control.get("target_effects") or []
    usable: list[tuple[dict[str, Any], float | None, float | None, float | None]] = []
    for effect in effects:
        before = _chart_numeric(effect.get("before"))
        after = _chart_numeric(effect.get("after"))
        expected = _chart_numeric(effect.get("expected"))
        if before is None and after is None and expected is None:
            continue
        usable.append((effect, before, after, expected))
    if not usable:
        return _chart_skip(
            heading, "no numeric before/after/expected values available", ctx
        )
    usable = usable[:limit] if limit else usable
    width = 760
    label_w = 180
    row_height = 96
    bar_max = width - label_w - 140
    height = 40 + row_height * len(usable)
    parts = [_chart_open(heading, width, height)]
    for idx, (effect, before, after, expected) in enumerate(usable):
        y = 32 + idx * row_height
        parts.append(
            f'<text class="chart-label" x="0" y="{y + 14}">'
            f"{h_escape(human_metric_name(effect.get('metric')))}</text>"
        )
        numeric_values = [v for v in (before, after, expected) if v is not None]
        local_max = max((abs(v) for v in numeric_values), default=1.0) or 1.0
        sub_rows = [
            ("before", before, effect.get("before"), "chart-before"),
            ("after", after, effect.get("after"), "chart-after"),
            ("expected", expected, effect.get("expected"), "chart-expected"),
        ]
        for sub_idx, (sub_label, sub_value, sub_raw, sub_class) in enumerate(sub_rows):
            row_y = y + sub_idx * 22
            if sub_value is None:
                parts.append(
                    f'<text class="chart-value" x="{label_w}" y="{row_y + 13}">'
                    f"{sub_label}: unavailable</text>"
                )
                continue
            bar_w = max(1, int(abs(sub_value) / local_max * bar_max))
            parts.append(
                f'<rect class="chart-bar {sub_class}" x="{label_w}" y="{row_y}"'
                f' width="{bar_w}" height="14" rx="2"></rect>'
            )
            parts.append(
                f'<text class="chart-value" x="{label_w + bar_w + 8}" y="{row_y + 12}">'
                f"{sub_label} {h_escape(human_number(sub_raw))}</text>"
            )
        parts.append(
            f'<text class="chart-value" x="{label_w}" y="{y + 74}">'
            f"status {h_escape(effect.get('status'))} · "
            f"confidence {h_escape(effect.get('confidence'))}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def timeseries_artifacts(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        artifact
        for artifact in artifacts
        if artifact.get("schema_version") == TIMESERIES_SCHEMA
        and isinstance(artifact.get("metrics"), list)
    ]


def spark_points(
    values: list[float], *, x: int, y: int, width: int, height: int
) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return f"{x},{y + height / 2:.1f}"
    min_value = min(values)
    max_value = max(values)
    span = max(max_value - min_value, 1.0)
    points: list[str] = []
    for index, value in enumerate(values):
        px = x + (index / (len(values) - 1)) * width
        py = y + height - ((value - min_value) / span) * height
        points.append(f"{px:.1f},{py:.1f}")
    return " ".join(points)


def html_timeseries_cards(
    artifacts: list[dict[str, Any]],
    limit: int,
    ctx: ReportContext,
    report_type: str,
) -> str:
    metrics: list[dict[str, Any]] = []
    for artifact in timeseries_artifacts(artifacts):
        for metric in artifact.get("metrics", []):
            if isinstance(metric, dict):
                metrics.append(metric)
    if not metrics:
        return ""
    metrics = metrics[:limit] if limit else metrics
    card_w = 340
    card_h = 138
    gap = 16
    cols = 2
    rows = (len(metrics) + cols - 1) // cols
    width = cols * card_w + (cols - 1) * gap
    height = 40 + rows * card_h + (rows - 1) * gap
    is_control = report_type == "control_review"
    heading = "Control Review Trend Cards" if is_control else "Posture Trend Cards"
    current_label = "After" if is_control else "Current"
    baseline_label = "Expected" if is_control else "Prior"
    section_label = (
        "Control review trend cards" if is_control else "Posture trend cards"
    )
    parts = [_chart_open(heading, width, height)]
    for index, metric in enumerate(metrics):
        col = index % cols
        row = index // cols
        x = col * (card_w + gap)
        y = 34 + row * (card_h + gap)
        points = metric.get("points")
        if not isinstance(points, list):
            ctx.warn("Trend card skipped a metric because points were unavailable.")
            continue
        current_values = [
            value
            for point in points
            if isinstance(point, dict)
            and (value := _chart_numeric(point.get("current"))) is not None
        ]
        baseline_values = [
            value
            for point in points
            if isinstance(point, dict)
            and (value := _chart_numeric(point.get("baseline"))) is not None
        ]
        if not current_values and not baseline_values:
            ctx.warn(
                "Trend card skipped a metric because no numeric values were available."
            )
            continue
        parts.append(
            f'<rect class="chart-card" x="{x}" y="{y}" width="{card_w}" height="{card_h}" rx="4"></rect>'
        )
        label = metric.get("label") or human_metric_name(metric.get("name"))
        parts.append(
            f'<text class="chart-label" x="{x + 12}" y="{y + 22}">{h_escape(label)}</text>'
        )
        current_value = metric.get("current")
        baseline_value = metric.get("baseline")
        pct_change = metric.get("pct_change")
        parts.append(
            f'<text class="chart-value" x="{x + 12}" y="{y + 44}">'
            f"{current_label} {h_escape(human_number(current_value))} vs "
            f"{baseline_label.lower()} {h_escape(human_number(baseline_value))}"
            "</text>"
        )
        parts.append(
            f'<text class="chart-value" x="{x + 12}" y="{y + 62}">'
            f"Delta vs {h_escape(baseline_label.lower())} "
            f"{h_escape(human_number(pct_change, percent=True))}</text>"
        )
        spark_x = x + 14
        spark_y = y + 78
        spark_w = card_w - 28
        spark_h = 42
        all_values = current_values + baseline_values
        min_value = min(all_values)
        max_value = max(all_values)
        span = max(max_value - min_value, 1.0)

        def scaled(values: list[float]) -> str:
            if not values:
                return ""
            if len(values) == 1:
                return f"{spark_x},{spark_y + spark_h / 2:.1f}"
            pts: list[str] = []
            for idx, value in enumerate(values):
                px = spark_x + (idx / (len(values) - 1)) * spark_w
                py = spark_y + spark_h - ((value - min_value) / span) * spark_h
                pts.append(f"{px:.1f},{py:.1f}")
            return " ".join(pts)

        parts.append(
            f'<polyline points="{scaled(baseline_values)}" fill="none" stroke="#85c1e9" stroke-width="2"></polyline>'
        )
        parts.append(
            f'<polyline points="{scaled(current_values)}" fill="none" stroke="#2474a6" stroke-width="2.5"></polyline>'
        )
    parts.append("</svg>")
    return (
        f'<section class="trend-cards" aria-label="{h_escape(section_label)}">'
        + "".join(parts)
        + "</section>"
    )


def html_window_timeline(artifacts: list[dict[str, Any]], report_type: str) -> str:
    rows: list[dict[str, Any]] = []
    is_control_report = report_type == "control_review"
    for artifact in artifacts:
        schema = artifact.get("schema_version")
        if schema not in {
            POSTURE_SCHEMA,
            TIMESERIES_SCHEMA,
            CONTROL_SCHEMA,
            SCORECARD_SCHEMA,
        }:
            continue
        if schema == CONTROL_SCHEMA:
            current = artifact.get("after_window")
            baseline = artifact.get("before_window")
        else:
            current = artifact.get("current_window")
            baselines = artifact.get("baseline_windows")
            if not isinstance(baselines, list) or not baselines:
                continue
            baseline = baselines[0]
        if not isinstance(current, dict) or not isinstance(baseline, dict):
            continue
        current_start = parse_utc_timestamp(current.get("start"))
        current_end = parse_utc_timestamp(current.get("end"))
        baseline_start = parse_utc_timestamp(baseline.get("start"))
        baseline_end = parse_utc_timestamp(baseline.get("end"))
        if not all((current_start, current_end, baseline_start, baseline_end)):
            continue
        is_control_row = schema == CONTROL_SCHEMA or (
            is_control_report and schema == TIMESERIES_SCHEMA
        )
        rows.append(
            {
                "label": artifact_display_name(artifact),
                "baseline_start": baseline_start,
                "baseline_end": baseline_end,
                "current_start": current_start,
                "current_end": current_end,
                "baseline_label": "Expected" if is_control_row else "Baseline",
                "current_label": "After" if is_control_row else "Current",
            }
        )
    if not rows:
        return ""
    if len(rows) > 1:
        endpoints = ("baseline_start", "baseline_end", "current_start", "current_end")
        first = rows[0]
        max_drift_seconds = max(
            abs((row[field] - first[field]).total_seconds())
            for row in rows[1:]
            for field in endpoints
        )
        if max_drift_seconds < 3600:
            rows = [
                {
                    "label": "Report comparison window",
                    "baseline_start": min(row["baseline_start"] for row in rows),
                    "baseline_end": max(row["baseline_end"] for row in rows),
                    "current_start": min(row["current_start"] for row in rows),
                    "current_end": max(row["current_end"] for row in rows),
                    "baseline_label": rows[0].get("baseline_label", "Baseline"),
                    "current_label": rows[0].get("current_label", "Current"),
                }
            ]
    min_start = min(row["baseline_start"] for row in rows)
    max_end = max(row["current_end"] for row in rows)
    total_seconds = max((max_end - min_start).total_seconds(), 1.0)
    width = 760
    label_w = 150
    plot_w = 560
    row_h = 50
    height = 54 + row_h * len(rows)

    def x_for(moment: datetime) -> int:
        return int(
            label_w + ((moment - min_start).total_seconds() / total_seconds) * plot_w
        )

    parts = [
        '<section class="window-timeline" aria-label="Evidence window timeline">',
        _chart_open("Evidence Window Timeline", width, height),
        f'<text class="chart-value" x="{label_w}" y="38">{h_escape(human_timestamp(min_start.isoformat().replace("+00:00", "Z")))}</text>',
        f'<text class="chart-value" x="{label_w + plot_w}" y="38" text-anchor="end">{h_escape(human_timestamp(max_end.isoformat().replace("+00:00", "Z")))}</text>',
    ]
    for index, row in enumerate(rows):
        y = 58 + index * row_h
        base_x = x_for(row["baseline_start"])
        base_w = max(2, x_for(row["baseline_end"]) - base_x)
        cur_x = x_for(row["current_start"])
        cur_w = max(2, x_for(row["current_end"]) - cur_x)
        parts.extend(
            [
                f'<text class="chart-label" x="0" y="{y + 17}">{h_escape(row["label"])}</text>',
                f'<line x1="{label_w}" y1="{y + 10}" x2="{label_w + plot_w}" y2="{y + 10}" stroke="#d8dee8" stroke-width="1"></line>',
                f'<rect class="timeline-baseline" x="{base_x}" y="{y}" width="{base_w}" height="20" rx="3"></rect>',
                f'<rect class="timeline-current" x="{cur_x}" y="{y}" width="{cur_w}" height="20" rx="3"></rect>',
                f'<text class="chart-value" x="{base_x + base_w / 2:.1f}" y="{y + 36}" text-anchor="middle">{h_escape(row.get("baseline_label", "Baseline"))}</text>',
                f'<text class="chart-value" x="{cur_x + cur_w / 2:.1f}" y="{y + 36}" text-anchor="middle">{h_escape(row.get("current_label", "Current"))}</text>',
            ]
        )
    parts.append("</svg></section>")
    return "".join(parts)


def html_chart_sections(
    report_type: str,
    selected: dict[str, Any],
    limit: int,
    ctx: ReportContext,
) -> str:
    pieces: list[str] = []
    if report_type == "executive_posture":
        posture = selected["posture"]
        pieces.append(html_metric_delta_cards(posture, limit, ctx))
        pieces.append(html_current_baseline_bars(posture, limit, ctx))
        if selected.get("index"):
            pieces.append(html_ranking_bars(selected["index"], limit, ctx))
        if selected.get("mover"):
            pieces.append(html_mover_bars(selected["mover"], limit, ctx))
        scorecards = selected.get("scorecards") or []
        if scorecards:
            pieces.append(html_domain_matrix(scorecards, limit, ctx))
    elif report_type == "soc_triage":
        pieces.append(html_ranking_bars(selected["index"], limit, ctx))
        scorecards = selected.get("scorecards") or []
        if scorecards:
            pieces.append(html_domain_matrix(scorecards, limit, ctx))
        else:
            pieces.append(
                _chart_skip(
                    "Domain Score Matrix",
                    "degraded SOC mode has no compatible scorecards",
                    ctx,
                )
            )
        if selected.get("mover"):
            pieces.append(html_mover_bars(selected["mover"], limit, ctx))
    elif report_type == "control_review":
        pieces.append(html_control_bars(selected["control"], limit, ctx))
    elif report_type == "scorecard_brief":
        pieces.append(html_scorecard_feature_cards(selected["scorecard"], limit, ctx))
    elif report_type in {"crawler_governance", "edge_ops_impact"}:
        scorecards = selected.get("scorecards") or []
        if selected.get("index"):
            pieces.append(html_ranking_bars(selected["index"], limit, ctx))
        else:
            pieces.append(html_scorecard_score_bars(scorecards, limit, ctx))
        pieces.append(html_domain_matrix(scorecards, limit, ctx))
        if report_type == "edge_ops_impact" and selected.get("posture"):
            pieces.append(html_current_baseline_bars(selected["posture"], limit, ctx))
        if report_type == "edge_ops_impact" and selected.get("mover"):
            pieces.append(html_mover_bars(selected["mover"], limit, ctx))
    body = "".join(piece for piece in pieces if piece)
    if not body:
        return ""
    return '<section class="charts" aria-label="Charts">' + body + "</section>"


def render_html(
    title: str,
    report_type: str,
    selected: dict[str, Any],
    all_artifacts: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    limit: int,
    ctx: ReportContext,
    *,
    scope_label: str | None = None,
) -> str:
    fleet_scorecard_brief = report_type == "scorecard_brief" and selected.get(
        "is_fleet"
    )
    if fleet_scorecard_brief:
        body = html_scorecard_fleet_report(
            title,
            selected,
            all_artifacts,
            notes,
            limit,
            ctx,
            scope_label,
        )
        chart_html = ""
    else:
        chart_html = html_chart_sections(report_type, selected, limit, ctx)
        markdown = render_markdown(
            title,
            report_type,
            selected,
            all_artifacts,
            notes,
            limit,
            ctx,
            scope_label=scope_label,
            include_metadata=False,
        )
        body = markdown_to_simple_html(markdown)
        timeline_html = html_window_timeline(all_artifacts, report_type)
        trend_html = html_timeseries_cards(all_artifacts, limit, ctx, report_type)
        trend_anchor = None
        if "<h2>Executive Summary</h2>" in body:
            trend_anchor = "<h2>Executive Summary</h2>"
        elif "<h2>Control Review Summary</h2>" in body:
            trend_anchor = "<h2>Control Review Summary</h2>"
        elif (
            report_type == "scorecard_brief"
            and "<h2>Selected Entity Context</h2>" in body
        ):
            trend_anchor = "<h2>Selected Entity Context</h2>"
        if (timeline_html or trend_html) and trend_anchor:
            body = body.replace(
                trend_anchor,
                timeline_html + trend_html + trend_anchor,
                1,
            )
        if report_type == "scorecard_brief":
            hero_html = html_scorecard_overall_gauge(selected["scorecard"], ctx)
            context_panel = html_scorecard_context_panel(selected)
            body = re.sub(
                r"<h2>Selected Entity Context</h2>.*?(?=<h2>Domain Scores</h2>)",
                "",
                body,
                count=1,
                flags=re.S,
            )
            if "</h1>" in body:
                body = body.replace("</h1>", "</h1>" + hero_html + context_panel, 1)
            if chart_html and "<h2>Domain Scores</h2>" in body:
                body = body.replace(
                    "<h2>Domain Scores</h2>", chart_html + "<h2>Domain Scores</h2>", 1
                )
                chart_html = ""
        if report_type == "scorecard_brief" and chart_html and trend_anchor:
            body = body.replace(trend_anchor, chart_html + trend_anchor, 1)
            chart_html = ""
    css = """
body{font-family:Arial,sans-serif;margin:0;color:#17202a;background:#f7f8fa}
main{max-width:1120px;margin:0 auto;padding:32px}
h1{font-size:34px;margin:0 0 8px}h2{margin-top:32px;border-top:1px solid #d9dee7;padding-top:20px}
table{border-collapse:collapse;width:100%;margin:12px 0;background:#fff}
th,td{border:1px solid #d8dee8;padding:8px;text-align:left;vertical-align:top}
th{background:#eef2f7}code{background:#eef2f7;padding:2px 4px;border-radius:3px}
.trend-cards{margin:20px 0 8px}
.window-timeline{margin:12px 0 20px}
.charts{display:grid;grid-gap:16px;margin:16px 0}
.chart{width:100%;background:#fff;border:1px solid #d8dee8;padding:12px;box-sizing:border-box}
.chart-title{font-weight:700;font-size:14px;fill:#17202a}
.chart-label{font-size:12px;fill:#17202a}
.chart-value{font-size:12px;fill:#2b3a4a}
.chart-large{font-size:20px;font-weight:700;fill:#17202a}
.score-hero{background:#fff;border:1px solid #d8dee8;margin:18px 0 24px;padding:18px;text-align:center}
.score-hero-label{font-size:12px;color:#5d6d7e;text-transform:uppercase;font-weight:700;letter-spacing:0}
.overall-gauge{width:220px;max-width:70vw;height:165px;margin:0 auto -8px;display:block}
.overall-gauge-track{fill:none;stroke:#e5eaf1;stroke-width:14;stroke-linecap:round}
.overall-gauge-fill{fill:none;stroke:#2474a6;stroke-width:14;stroke-linecap:round}
.overall-gauge-metric{font-size:46px;font-weight:700;fill:#17202a;dominant-baseline:middle}
.score-delta-up{font-size:15px;font-weight:700;color:#1f8f3a;text-align:center}
.score-delta-down{font-size:15px;font-weight:700;color:#b4232f;text-align:center}
.score-delta-neutral{font-size:15px;font-weight:700;color:#5d6d7e;text-align:center}
.entity-identity{background:#fff;border:1px solid #d8dee8;padding:16px;margin:0 0 20px}
.entity-dimension{font-size:12px;color:#5d6d7e;text-transform:uppercase;font-weight:700}
.entity-name{font-size:26px;font-weight:700;color:#17202a;margin:4px 0;overflow-wrap:anywhere}
.entity-identity p{margin:0 0 12px;color:#2b3a4a}
.entity-metadata-row{display:flex;flex-wrap:wrap;align-items:center;row-gap:6px;margin-top:2px}
.entity-metadata-item{display:inline-flex;gap:6px;align-items:baseline;padding:0 12px 0 0;margin-right:12px;color:#17202a}
.entity-metadata-item + .entity-metadata-item{border-left:1px solid #cfd7e2;padding-left:12px}
.entity-metadata-label{font-size:11px;color:#5d6d7e;text-transform:uppercase;font-weight:700}
.entity-metadata-value{font-size:13px;color:#17202a;font-weight:700}
.rule-matrix{background:#fff;border:1px solid #d8dee8;padding:16px}
.rule-matrix h2{border:0;margin:0 0 14px;padding:0;font-size:18px}
.rule-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.rule-card{border:1px solid #d8dee8;border-radius:6px;padding:12px;min-height:132px;background:#fff;display:flex;flex-direction:column;gap:6px}
.rule-card-top{display:flex;align-items:center;justify-content:space-between;gap:8px}
.rule-domain,.rule-status{font-size:11px;color:#5d6d7e;text-transform:uppercase}
.rule-status{text-align:center}
.rule-status-triggered{color:#b4232f;font-weight:700}
.rule-name{font-size:13px;font-weight:700;color:#17202a}
.rule-condition{font-size:12px;color:#5d6d7e;min-height:16px}
.rule-gauge{width:120px;height:90px;margin:-6px 0 -10px;align-self:center}
.gauge-track{fill:none;stroke:#e5eaf1;stroke-width:8;stroke-linecap:round}
.gauge-fill{fill:none;stroke:#2474a6;stroke-width:8;stroke-linecap:round}
.gauge-metric{font-size:20px;font-weight:700;fill:#17202a;dominant-baseline:middle}
.rule-points{border:1px solid #cfd7e2;border-radius:999px;padding:2px 7px;font-size:12px;font-weight:700;color:#17202a;background:#f7f8fa;white-space:nowrap}
.rule-delta-up{font-size:13px;font-weight:700;color:#1f8f3a;text-align:center}
.rule-delta-down{font-size:13px;font-weight:700;color:#b4232f;text-align:center}
.rule-delta-neutral{font-size:13px;font-weight:700;color:#5d6d7e;text-align:center}
.chart-bar{fill:#2474a6}
.chart-current{fill:#2474a6}
.chart-baseline{fill:#85c1e9}
.chart-before{fill:#f5b041}
.chart-after{fill:#2474a6}
.chart-expected{fill:#7fb3d5}
.chart-card{fill:#fff;stroke:#d8dee8}
.chart-cell{fill:#2474a6}
.timeline-baseline{fill:#85c1e9}
.timeline-current{fill:#2474a6}
.chart-skip{background:#fff3cd;border:1px solid #f0d27a;padding:10px;color:#5a4412;margin:12px 0}
.fleet-header{background:#fff;border:1px solid #d8dee8;padding:14px 16px;margin:14px 0 16px;display:flex;flex-wrap:wrap;gap:8px}
.fleet-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:16px 0 20px}
.fleet-kpi{background:#fff;border:1px solid #d8dee8;padding:14px}
.fleet-kpi-label{font-size:11px;color:#5d6d7e;text-transform:uppercase;font-weight:700}
.fleet-kpi-value{font-size:24px;font-weight:700;color:#17202a;margin-top:4px}
.fleet-findings ul{background:#fff;border:1px solid #d8dee8;padding:14px 18px 14px 32px}
.coverage-bar{display:flex;height:14px;min-width:120px;background:#eef2f7;border:1px solid #d8dee8}
.coverage-segment{display:block;height:14px}
.coverage-triggered{background:#b4232f}
.coverage-evaluated-zero{background:#85c1e9}
.coverage-missing-input{background:#f5b041}
""".strip()
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{h_escape(title)}</title><style>{css}</style></head><body><main>"
        + body
        + chart_html
        + "</main></body></html>\n"
    )


def markdown_to_simple_html(markdown: str) -> str:
    lines = markdown.splitlines()
    output: list[str] = []
    table_lines: list[str] = []
    list_open = False

    def flush_table() -> None:
        nonlocal table_lines
        if not table_lines:
            return
        output.append(table_to_html(table_lines))
        table_lines = []

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            output.append("</ul>")
            list_open = False

    for line in lines:
        if line.startswith("|"):
            close_list()
            table_lines.append(line)
            continue
        flush_table()
        if not line.strip():
            close_list()
            continue
        if line.startswith("# "):
            close_list()
            output.append(f"<h1>{h_escape(_demd(line[2:]))}</h1>")
        elif line.startswith("## "):
            close_list()
            output.append(f"<h2>{h_escape(_demd(line[3:]))}</h2>")
        elif line.startswith("### "):
            close_list()
            output.append(f"<h3>{h_escape(_demd(line[4:]))}</h3>")
        elif line.startswith("- "):
            if not list_open:
                output.append("<ul>")
                list_open = True
            output.append(f"<li>{inline_html(line[2:])}</li>")
        else:
            close_list()
            output.append(f"<p>{inline_html(line)}</p>")
    flush_table()
    close_list()
    return "".join(output)


def inline_html(text: str) -> str:
    parts: list[str] = []

    def append_text(segment: str) -> None:
        cursor = 0
        while cursor < len(segment):
            start = _find_unescaped(segment, "_", cursor)
            if start == -1:
                parts.append(h_escape(_demd(segment[cursor:])))
                return
            end = _find_unescaped(segment, "_", start + 1)
            if end == -1:
                parts.append(h_escape(_demd(segment[cursor:])))
                return
            parts.append(h_escape(_demd(segment[cursor:start])))
            parts.append(f"<em>{h_escape(_demd(segment[start + 1 : end]))}</em>")
            cursor = end + 1

    cursor = 0
    while cursor < len(text):
        start = _find_unescaped(text, "`", cursor)
        if start == -1:
            append_text(text[cursor:])
            break
        end = _find_unescaped(text, "`", start + 1)
        if end == -1:
            append_text(text[cursor:])
            break
        append_text(text[cursor:start])
        parts.append(f"<code>{h_escape(_demd(text[start + 1 : end]))}</code>")
        cursor = end + 1
    return "".join(parts)


def _split_table_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    cells: list[str] = []
    buffer: list[str] = []
    index = 0
    while index < len(body):
        ch = body[index]
        if ch == "\\" and index + 1 < len(body):
            buffer.append(body[index])
            buffer.append(body[index + 1])
            index += 2
            continue
        if ch == "|":
            cells.append("".join(buffer).strip())
            buffer = []
            index += 1
            continue
        buffer.append(ch)
        index += 1
    cells.append("".join(buffer).strip())
    return cells


def table_to_html(lines: list[str]) -> str:
    rows = []
    for line in lines:
        cells = _split_table_row(line)
        if cells and all(set(cell) <= {"-"} for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        return ""
    header = rows[0]
    body = rows[1:]
    output = ["<table><thead><tr>"]
    output.extend(f"<th>{h_escape(_demd(cell))}</th>" for cell in header)
    output.append("</tr></thead><tbody>")
    for row in body:
        output.append("<tr>")
        output.extend(f"<td>{inline_html(cell)}</td>" for cell in row)
        output.append("</tr>")
    output.append("</tbody></table>")
    return "".join(output)


def _render_via_engine(
    *,
    report_type: str,
    value: Any,
    artifacts: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    ctx: ReportContext,
) -> str | None:
    """Route HTML rendering through the report_engine for a given
    wrapper ``report_type``. Returns ``None`` when the engine can't
    accept this input (raw artifact mode, missing schema, registry miss)
    so the caller can fall back to the legacy markdown→HTML path.
    """
    try:
        from report_engine import render as engine_render
        from report_engine.contexts import REPORT_TYPE_REGISTRY
    except ImportError:
        return None

    is_wrapper = (
        isinstance(value, dict)
        and value.get("schema_version") == "bot_report_input.v1"
        and value.get("report_type") == report_type
    )
    if not is_wrapper:
        return None

    module = REPORT_TYPE_REGISTRY.get(report_type)
    if module is None:
        return None

    try:
        artifact = module.assemble(artifacts)
    except (ValueError, KeyError) as exc:
        ctx.warnings.append(
            f"{report_type} report_engine assembly failed ({exc}); "
            "falling back to legacy markdown path."
        )
        return None

    notes_by_slot = engine_render._build_notes_by_slot(
        notes,
        getattr(module, "NOTE_ID_TO_SLOT", {}),
    )
    template_ctx = module.prepare(artifact)
    template_ctx["notes_by_slot"] = notes_by_slot
    template_ctx["mode"] = "full"

    env = engine_render.build_env()
    template = env.get_template(module.TEMPLATE)
    return template.render(**template_ctx)


def _render_executive_posture_via_engine(
    value: Any,
    artifacts: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    ctx: ReportContext,
) -> str | None:
    """Backwards-compat shim — kept so any external caller still works."""
    return _render_via_engine(
        report_type="executive_posture",
        value=value,
        artifacts=artifacts,
        notes=notes,
        ctx=ctx,
    )


def render(
    value: Any,
    args: argparse.Namespace,
) -> tuple[str, list[str]]:
    ctx = ReportContext()
    (
        artifacts,
        notes,
        wrapper_report_type,
        wrapper_title,
        wrapper_limit,
        scope_label,
        raw_mode,
    ) = load_report_input(value, args, ctx)
    report_type, title, limit, resolved_scope_label = resolve_options(
        artifacts,
        wrapper_report_type=wrapper_report_type,
        wrapper_title=wrapper_title,
        wrapper_limit=wrapper_limit,
        scope_label=scope_label,
        raw_mode=raw_mode,
        args=args,
        ctx=ctx,
    )
    artifacts = dedupe_artifact_bodies(artifacts, notes, report_type, ctx)
    selected = validate_report_artifacts(report_type, artifacts, ctx)
    scan_metadata_warnings(artifacts, ctx)
    validate_analyst_notes(notes, artifacts)
    if args.format == "html":
        # Dual-route: HTML for ``executive_posture``, ``soc_triage``,
        # ``crawler_governance``, and ``edge_ops_impact`` goes through
        # the new report_engine path. Markdown callers (``md_executive``,
        # ``md_soc``, ``md_domain_report`` for crawler and edge) stay on
        # the legacy path. Other report types are unchanged.
        if report_type in {
            "executive_posture",
            "soc_triage",
            "crawler_governance",
            "edge_ops_impact",
        }:
            engine_html = _render_via_engine(
                report_type=report_type,
                value=value,
                artifacts=artifacts,
                notes=notes,
                ctx=ctx,
            )
            if engine_html is not None:
                return engine_html, ctx.warnings
        return (
            render_html(
                title,
                report_type,
                selected,
                artifacts,
                notes,
                limit,
                ctx,
                scope_label=resolved_scope_label,
            ),
            ctx.warnings,
        )
    return (
        render_markdown(
            title,
            report_type,
            selected,
            artifacts,
            notes,
            limit,
            ctx,
            scope_label=resolved_scope_label,
        ),
        ctx.warnings,
    )


def main() -> int:
    args = parse_args()
    try:
        value = json.loads(read_input(args))
        output, warnings = render(value, args)
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            print(output, end="")
        for warning in warnings:
            print(f"WARNING: {warning}", file=sys.stderr)
    except (OSError, ReportError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
