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
import re
import sys
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

SUPPORTED_SCHEMAS = {
    POSTURE_SCHEMA,
    MOVER_SCHEMA,
    CONTROL_SCHEMA,
    SCORECARD_SCHEMA,
    INDEX_SCHEMA,
    SCORECARD_PACKET_SCHEMA,
}
KNOWN_UNSUPPORTED_SCHEMAS = {TIMESERIES_SCHEMA}
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
            ctx.artifact_id_explicit.get(artifact_id)
            for artifact_id in duplicate_ids
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
        return {
            "posture": posture,
            "index": filter_compatible_companion(posture, index, "index", ctx),
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
        scorecard = require_one(artifacts, SCORECARD_SCHEMA, report_type)
        index = first_or_warn(artifacts, INDEX_SCHEMA, report_type, ctx)
        if index:
            compatible_scorecard = compatible_scorecards_for_index(
                index, [scorecard], ctx, required=True
            )[0]
            scorecard = compatible_scorecard
        return {"scorecard": scorecard, "index": index}
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
    for key in ("current_window", "before_window", "after_window", "expected_window"):
        if artifact.get(key):
            parts.append(f"{key}: {stringify(artifact[key])}")
    if artifact.get("baseline_windows"):
        parts.append(f"baseline_windows: {stringify(artifact['baseline_windows'])}")
    return "; ".join(parts) if parts else "unavailable"


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
) -> str:
    scope_text = resolve_scope_display(scope_label, selected, ctx)
    parts = [
        f"# {md_escape(title)}",
        "",
        f"Report type: `{report_type}`",
        f"Scope: {md_escape(scope_text)}",
        "",
    ]
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
    parts.append(md_analyst_notes(notes, all_artifacts, ctx))
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
    rows = [
        [
            metric.get("name"),
            metric.get("current"),
            metric.get("baseline"),
            metric.get("absolute_delta"),
            metric.get("pct_change"),
            metric.get("direction"),
            metric.get("confidence"),
        ]
        for metric in metrics
    ]
    parts = [
        "## Executive Summary",
        "Movement-only posture report based on emitted artifact fields. It does not infer cause.",
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
    mover = selected.get("mover")
    if mover:
        parts.extend(["## Movers", md_movers(mover, limit, ctx)])
    return "\n\n".join(parts)


def md_soc(selected: dict[str, Any], limit: int, ctx: ReportContext) -> str:
    parts = ["## Top Risky Entities", md_ranking(selected["index"], limit, ctx)]
    scorecards = selected.get("scorecards") or []
    if scorecards:
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
                effect.get("metric"),
                effect.get("before"),
                effect.get("after"),
                effect.get("expected"),
                effect.get("absolute_delta_vs_expected"),
                effect.get("pct_change_vs_expected"),
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
        parts.extend(
            [
                "## Confidence",
                f"Expected basis: {md_escape(basis)}. This is an effectiveness review, not proof of cause.",
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
    parts = [
        "## Entity",
        md_table(
            ["Entity type", "Entity", "Score", "Band", "Primary domain", "Confidence"],
            [
                [
                    card.get("entity_type"),
                    card.get("entity"),
                    card.get("score"),
                    card.get("band"),
                    card.get("primary_domain"),
                    card.get("confidence"),
                ]
            ],
        ),
        "## Domain Scores",
        md_table(
            ["Domain", "Score"],
            [
                [domain, score]
                for domain, score in (card.get("domain_scores") or {}).items()
            ],
        ),
        "## Feature Evidence",
        md_feature_rows(card.get("features", []))
        if card.get("features")
        else "No evaluated features crossed thresholds.",
        "## Not Evaluated Features",
        md_missing_rows(card.get("not_evaluated_features", []))
        if card.get("not_evaluated_features")
        else "No missing feature inputs reported.",
    ]
    steps = card.get("recommended_next_steps")
    if isinstance(steps, list) and steps:
        parts.extend(
            [
                "## Recommended Next Steps",
                "\n".join(f"- {md_escape(step)}" for step in steps),
            ]
        )
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
    rows = [
        [
            feature.get("domain"),
            feature.get("name"),
            feature.get("points"),
            feature.get("evidence"),
        ]
        for feature in features
    ]
    return md_table(["Domain", "Feature", "Points", "Evidence"], rows)


def md_missing_rows(missing: list[dict[str, Any]]) -> str:
    rows = [
        [
            feature.get("domain"),
            feature.get("name"),
            ", ".join(str(item) for item in feature.get("missing_inputs", [])),
            feature.get("reason"),
        ]
        for feature in missing
    ]
    return md_table(["Domain", "Feature", "Missing inputs", "Reason"], rows)


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
        sources = note.get("data_sources")
        if not isinstance(sources, list) or not sources:
            ctx.warn(
                f"Analyst note {note.get('note_id', index)} has no cited data sources."
            )
            continue
        citations = []
        for source in sources:
            artifact, normalized_pointer, resolved = resolve_citation(source, artifacts)
            artifact_label = artifact.get("artifact_id")
            citations.append(
                f"- {md_escape(source.get('label', normalized_pointer or 'citation'))}: "
                f"{md_escape(artifact_label)} {md_escape(normalized_pointer)} = {md_escape(resolved)}"
            )
        if citations:
            parts.append("\n".join(citations))
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
            f"{h_escape(metric.get('name'))}</text>"
        )
        parts.append(
            f'<text class="chart-value" x="{x + 12}" y="{y + 46}">'
            f"Current: {h_escape(metric.get('current'))}</text>"
        )
        parts.append(
            f'<text class="chart-value" x="{x + 12}" y="{y + 64}">'
            f"Baseline: {h_escape(metric.get('baseline'))}</text>"
        )
        parts.append(
            f'<text class="chart-value" x="{x + 12}" y="{y + 82}">'
            f"Delta: {h_escape(metric.get('absolute_delta'))}</text>"
        )
        parts.append(
            f'<text class="chart-value" x="{x + 12}" y="{y + 100}">'
            f"Pct change: {h_escape(metric.get('pct_change'))}</text>"
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
            f"{h_escape(metric.get('name'))}</text>"
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
            f"current {h_escape(metric.get('current'))}</text>"
        )
        parts.append(
            f'<rect class="chart-bar chart-baseline" x="{label_w}" y="{y + 22}"'
            f' width="{base_w}" height="16" rx="2"></rect>'
        )
        parts.append(
            f'<text class="chart-value" x="{label_w + base_w + 8}" y="{y + 35}">'
            f"baseline {h_escape(metric.get('baseline'))}</text>"
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
            f"{stringify(card.get('entity'))} "
            f"({stringify(card.get('entity_type'))})"
        )
        display = (
            f"sorted by emitted score · score {stringify(card.get('score'))}"
            f" · band {stringify(card.get('band'))}"
            f" · primary {stringify(card.get('primary_domain'))}"
            f" · confidence {stringify(card.get('confidence'))}"
        )
        rows.append((label, score, display))
    if not rows:
        return _chart_skip(heading, "no numeric scorecard scores available", ctx)
    rows.sort(key=lambda item: (-item[1], item[0]))
    rows = rows[:limit] if limit else rows
    return _horizontal_bars_svg(heading, rows)


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
            f"{h_escape(effect.get('metric'))}</text>"
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
                f"{sub_label} {h_escape(sub_raw)}</text>"
            )
        parts.append(
            f'<text class="chart-value" x="{label_w}" y="{y + 74}">'
            f"status {h_escape(effect.get('status'))} · "
            f"confidence {h_escape(effect.get('confidence'))}</text>"
        )
    parts.append("</svg>")
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
        pieces.append(html_scorecard_domain_bars(selected["scorecard"], ctx))
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
    )
    body = markdown_to_simple_html(markdown)
    css = """
body{font-family:Arial,sans-serif;margin:0;color:#17202a;background:#f7f8fa}
main{max-width:1120px;margin:0 auto;padding:32px}
h1{font-size:34px;margin:0 0 8px}h2{margin-top:32px;border-top:1px solid #d9dee7;padding-top:20px}
table{border-collapse:collapse;width:100%;margin:12px 0;background:#fff}
th,td{border:1px solid #d8dee8;padding:8px;text-align:left;vertical-align:top}
th{background:#eef2f7}code{background:#eef2f7;padding:2px 4px;border-radius:3px}
.charts{display:grid;grid-gap:16px;margin:16px 0}
.chart{width:100%;background:#fff;border:1px solid #d8dee8;padding:12px;box-sizing:border-box}
.chart-title{font-weight:700;font-size:14px;fill:#17202a}
.chart-label{font-size:12px;fill:#17202a}
.chart-value{font-size:12px;fill:#2b3a4a}
.chart-bar{fill:#2474a6}
.chart-current{fill:#2474a6}
.chart-baseline{fill:#85c1e9}
.chart-before{fill:#f5b041}
.chart-after{fill:#2474a6}
.chart-expected{fill:#7fb3d5}
.chart-card{fill:#fff;stroke:#d8dee8}
.chart-cell{fill:#2474a6}
.chart-skip{background:#fff3cd;border:1px solid #f0d27a;padding:10px;color:#5a4412;margin:12px 0}
""".strip()
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{h_escape(title)}</title><style>{css}</style></head><body><main>"
        + chart_html
        + body
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
            parts.append(f"<em>{h_escape(_demd(segment[start + 1:end]))}</em>")
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
        parts.append(f"<code>{h_escape(_demd(text[start + 1:end]))}</code>")
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
