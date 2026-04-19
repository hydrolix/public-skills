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


def md_escape(value: Any) -> str:
    text = stringify(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def h_escape(value: Any) -> str:
    return html.escape(html.unescape(stringify(value)), quote=True)


def slug_title(report_type: str) -> str:
    return report_type.replace("_", " ").title()


def json_fingerprint(value: Any) -> str:
    sanitized = copy.deepcopy(value)
    if isinstance(sanitized, dict):
        sanitized.pop("artifact_id", None)
    return json.dumps(sanitized, sort_keys=True, separators=(",", ":"))


def reserved_artifact_id(artifact_id: str) -> bool:
    return RESERVED_CHILD_ID.search(artifact_id) is not None


def schema_of(artifact: Any) -> str:
    if isinstance(artifact, dict):
        return str(artifact.get("schema_version", ""))
    return ""


def validate_artifact_schema(artifact: Any, allow_unknown: bool, ctx: ReportContext) -> bool:
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
    explicit_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []

    def append_unique(artifact: dict[str, Any]) -> None:
        artifact_id = str(artifact["artifact_id"])
        if artifact_id in explicit_ids:
            raise ReportError(f"Duplicate normalized artifact_id {artifact_id}.")
        explicit_ids.add(artifact_id)
        normalized.append(artifact)

    for index, raw in enumerate(artifacts, start=1):
        if not validate_artifact_schema(raw, allow_unknown, ctx):
            continue
        artifact_id = str(raw.get("artifact_id") or f"artifact-{index}")
        if reserved_artifact_id(artifact_id):
            raise ReportError(
                f"Artifact ID {artifact_id} uses a reserved generated child suffix."
            )
        if artifact_id in explicit_ids:
            raise ReportError(f"Duplicate artifact_id {artifact_id}.")

        parent = artifact_with_id(raw, artifact_id)
        append_unique(parent)

        if schema_of(raw) == SCORECARD_PACKET_SCHEMA:
            packet_index = raw.get("index")
            if isinstance(packet_index, dict) and schema_of(packet_index) == INDEX_SCHEMA:
                child = copy.deepcopy(packet_index)
                append_unique(
                    artifact_with_id(
                        child,
                        f"{artifact_id}#index",
                        parent_id=artifact_id,
                        parent_pointer="/index",
                    )
                )
            scorecards = raw.get("scorecards")
            if isinstance(scorecards, list):
                for child_index, scorecard in enumerate(scorecards, start=1):
                    if not isinstance(scorecard, dict) or schema_of(scorecard) != SCORECARD_SCHEMA:
                        continue
                    child = copy.deepcopy(scorecard)
                    append_unique(
                        artifact_with_id(
                            child,
                            f"{artifact_id}#scorecard-{child_index}",
                            parent_id=artifact_id,
                            parent_pointer=f"/scorecards/{child_index - 1}",
                        )
                    )

    seen_fingerprints: dict[str, str] = {}
    for artifact in normalized:
        fingerprint = json_fingerprint(artifact)
        artifact_id = str(artifact["artifact_id"])
        existing = seen_fingerprints.get(fingerprint)
        if existing:
            ctx.warn(
                f"Artifact {artifact_id} duplicates artifact {existing}; both are retained for deterministic selection."
            )
        else:
            seen_fingerprints[fingerprint] = artifact_id
    return normalized


def load_report_input(
    value: Any,
    args: argparse.Namespace,
    ctx: ReportContext,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None, str | None, int | None, str | None, str | None]:
    wrapper_report_type: str | None = None
    wrapper_title: str | None = None
    wrapper_limit: int | None = None
    scope_label: str | None = None
    notes: list[dict[str, Any]] = []
    raw_mode: str | None = None

    if isinstance(value, dict) and value.get("schema_version") == WRAPPER_SCHEMA:
        wrapper_report_type = value.get("report_type")
        if wrapper_report_type is not None and wrapper_report_type not in REPORT_TYPES:
            raise ReportError(f"Unsupported wrapper report_type {wrapper_report_type}.")
        wrapper_title = value.get("title")
        wrapper_limit = value.get("limit")
        if wrapper_limit is not None and (
            not isinstance(wrapper_limit, int) or isinstance(wrapper_limit, bool) or wrapper_limit <= 0
        ):
            raise ReportError("Wrapper limit must be a positive integer.")
        scope_label = value.get("scope_label")
        raw_notes = value.get("analyst_notes", [])
        if raw_notes is None:
            raw_notes = []
        if not isinstance(raw_notes, list) or not all(isinstance(note, dict) for note in raw_notes):
            raise ReportError("Wrapper analyst_notes must be an array of objects.")
        notes = raw_notes
        raw_artifacts = value.get("artifacts")
        if not isinstance(raw_artifacts, list) or not raw_artifacts:
            raise ReportError("Wrapper artifacts must be a non-empty array.")
    elif isinstance(value, dict) and "schema_version" in value:
        raw_mode = "single"
        raw_artifacts = [value]
    elif isinstance(value, list) and value:
        raw_mode = "array"
        raw_artifacts = value
        if args.report_type is None:
            raise ReportError("Raw artifact array input requires --report-type.")
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
    return normalized, notes, wrapper_report_type, wrapper_title, wrapper_limit, scope_label, raw_mode


def infer_report_type(artifacts: list[dict[str, Any]], raw_hint: str | None) -> str | None:
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
    if wrapper_report_type and cli_report_type and wrapper_report_type != cli_report_type:
        raise ReportError(
            f"Wrapper report_type {wrapper_report_type} conflicts with CLI --report-type {cli_report_type}."
        )
    report_type = wrapper_report_type or cli_report_type
    if report_type is None:
        report_type = infer_report_type(artifacts, raw_mode)
    if report_type is None:
        raise ReportError("Missing or ambiguous report intent; supply --report-type or wrapper report_type.")

    if args.title is not None and wrapper_title is not None and args.title != wrapper_title:
        ctx.warn("CLI --title overrides wrapper title.")
    title = args.title or wrapper_title or generated_title(report_type, artifacts, scope_label)

    if args.limit is not None and args.limit <= 0:
        raise ReportError("--limit must be a positive integer.")
    if args.limit is not None and wrapper_limit is not None and args.limit != wrapper_limit:
        ctx.warn("CLI --limit overrides wrapper limit.")
    limit = args.limit or wrapper_limit or default_limit(report_type)
    return report_type, title, limit, scope_label


def default_limit(report_type: str) -> int:
    if report_type == "scorecard_brief":
        return 20
    return 10


def generated_title(report_type: str, artifacts: list[dict[str, Any]], scope_label: str | None) -> str:
    scope = scope_label
    if not scope:
        for artifact in artifacts:
            scope_value = artifact.get("scope")
            if isinstance(scope_value, dict) and scope_value:
                scope = ", ".join(f"{key}={value}" for key, value in sorted(scope_value.items()))
                break
    if scope:
        return f"{slug_title(report_type)} - {scope}"
    return slug_title(report_type)


def by_schema(artifacts: list[dict[str, Any]], schema: str) -> list[dict[str, Any]]:
    return [artifact for artifact in artifacts if schema_of(artifact) == schema]


def require_one(artifacts: list[dict[str, Any]], schema: str, report_type: str) -> dict[str, Any]:
    matches = by_schema(artifacts, schema)
    if not matches:
        raise ReportError(f"{report_type} requires {schema}.")
    if len(matches) > 1:
        raise ReportError(f"{report_type} requires one {schema}; found {len(matches)}.")
    return matches[0]


def validate_report_artifacts(
    report_type: str,
    artifacts: list[dict[str, Any]],
    ctx: ReportContext,
) -> dict[str, Any]:
    if report_type == "executive_posture":
        return {
            "posture": require_one(artifacts, POSTURE_SCHEMA, report_type),
            "index": first_or_warn(artifacts, INDEX_SCHEMA, report_type, ctx),
            "mover": first_or_warn(artifacts, MOVER_SCHEMA, report_type, ctx),
        }
    if report_type == "soc_triage":
        index = require_one(artifacts, INDEX_SCHEMA, report_type)
        scorecards = by_schema(artifacts, SCORECARD_SCHEMA)
        scorecards = compatible_scorecards_for_index(index, scorecards, ctx, required=bool(scorecards))
        if not scorecards:
            ctx.warn("SOC triage has only bot_scorecard_index.v1 and renders a degraded ranking-only report.")
        return {"index": index, "scorecards": scorecards, "posture": first_or_warn(artifacts, POSTURE_SCHEMA, report_type, ctx), "mover": first_or_warn(artifacts, MOVER_SCHEMA, report_type, ctx)}
    if report_type == "control_review":
        return {"control": require_one(artifacts, CONTROL_SCHEMA, report_type)}
    if report_type == "scorecard_brief":
        scorecard = require_one(artifacts, SCORECARD_SCHEMA, report_type)
        index = first_or_warn(artifacts, INDEX_SCHEMA, report_type, ctx)
        if index:
            compatible_scorecard = compatible_scorecards_for_index(index, [scorecard], ctx, required=True)[0]
            scorecard = compatible_scorecard
        return {"scorecard": scorecard, "index": index}
    if report_type == "crawler_governance":
        scorecards = by_schema(artifacts, SCORECARD_SCHEMA)
        if not scorecards:
            raise ReportError("crawler_governance requires bot_entity_scorecard.v1 artifacts or a scorecard packet.")
        index = first_or_warn(artifacts, INDEX_SCHEMA, report_type, ctx)
        if index:
            scorecards = compatible_scorecards_for_index(index, scorecards, ctx, required=False)
        return {"scorecards": scorecards, "index": index, "posture": first_or_warn(artifacts, POSTURE_SCHEMA, report_type, ctx), "mover": first_or_warn(artifacts, MOVER_SCHEMA, report_type, ctx)}
    if report_type == "edge_ops_impact":
        scorecards = by_schema(artifacts, SCORECARD_SCHEMA)
        if not scorecards:
            raise ReportError("edge_ops_impact requires bot_entity_scorecard.v1 artifacts or a scorecard packet.")
        index = first_or_warn(artifacts, INDEX_SCHEMA, report_type, ctx)
        if index:
            scorecards = compatible_scorecards_for_index(index, scorecards, ctx, required=False)
        return {"scorecards": scorecards, "index": index, "posture": first_or_warn(artifacts, POSTURE_SCHEMA, report_type, ctx), "mover": first_or_warn(artifacts, MOVER_SCHEMA, report_type, ctx)}
    raise ReportError(f"Unsupported report type {report_type}.")


def known(value: Any) -> bool:
    return value not in (None, "", [], {})


def same_packet(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_parent = left.get("parent_artifact_id")
    right_parent = right.get("parent_artifact_id")
    return bool(left_parent and left_parent == right_parent)


def shared_metadata_matches(index: dict[str, Any], scorecard: dict[str, Any], ctx: ReportContext) -> bool:
    if same_packet(index, scorecard):
        for field in ("scope", "current_window", "baseline_windows", "table_used", "comparison_type"):
            left = index.get(field)
            right = scorecard.get(field)
            if known(left) and known(right) and left != right:
                raise ReportError(f"Same-packet scorecard metadata mismatch for {field}.")
            if not known(left) or not known(right):
                ctx.warn(f"{scorecard.get('artifact_id')} missing same-packet {field} metadata.")
        return True

    for field in ("scope", "current_window", "baseline_windows", "table_used"):
        left = index.get(field)
        right = scorecard.get(field)
        if not known(left) or not known(right):
            raise ReportError(f"Standalone scorecard pairing requires known {field} metadata.")
        if left != right:
            raise ReportError(f"Scorecard metadata mismatch for {field}.")

    left_comparison = index.get("comparison_type")
    right_comparison = scorecard.get("comparison_type")
    if known(left_comparison) != known(right_comparison):
        raise ReportError("Standalone scorecard pairing requires matching comparison_type metadata when present.")
    if known(left_comparison) and left_comparison != right_comparison:
        raise ReportError("Scorecard metadata mismatch for comparison_type.")
    return True


def compatible_scorecards_for_index(
    index: dict[str, Any],
    scorecards: list[dict[str, Any]],
    ctx: ReportContext,
    *,
    required: bool,
) -> list[dict[str, Any]]:
    by_key = {
        (str(card.get("entity_type")), str(card.get("entity"))): card
        for card in scorecards
        if known(card.get("entity_type")) and known(card.get("entity"))
    }
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
        ctx.warn("No scorecards were compatible with the selected index; using input order.")
        return scorecards
    return compatible


def first_or_warn(
    artifacts: list[dict[str, Any]],
    schema: str,
    report_type: str,
    ctx: ReportContext,
) -> dict[str, Any] | None:
    matches = by_schema(artifacts, schema)
    if len(matches) > 1:
        raise ReportError(f"{report_type} cannot select between multiple {schema} artifacts.")
    return matches[0] if matches else None


def scan_metadata_warnings(artifacts: list[dict[str, Any]], ctx: ReportContext) -> None:
    for artifact in artifacts:
        schema = schema_of(artifact)
        aid = artifact.get("artifact_id")
        if schema in {POSTURE_SCHEMA, SCORECARD_SCHEMA, INDEX_SCHEMA}:
            if "current_window" not in artifact or not artifact.get("current_window"):
                ctx.warn(f"{aid} missing current_window metadata.")
            if "baseline_windows" not in artifact or not artifact.get("baseline_windows"):
                ctx.warn(f"{aid} missing baseline_windows metadata.")
        elif schema == CONTROL_SCHEMA:
            if "before_window" not in artifact or not artifact.get("before_window"):
                ctx.warn(f"{aid} missing before_window metadata.")
            if "after_window" not in artifact or not artifact.get("after_window"):
                ctx.warn(f"{aid} missing after_window metadata.")
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


def limited_rows(rows: list[Any], limit: int, label: str, ctx: ReportContext) -> list[Any]:
    if limit > 0 and len(rows) > limit:
        ctx.warn(f"Showing {limit} of {len(rows)} available {label}; display limit omitted {len(rows) - limit}.")
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


def render_markdown(
    title: str,
    report_type: str,
    selected: dict[str, Any],
    all_artifacts: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    limit: int,
    ctx: ReportContext,
) -> str:
    parts = [f"# {md_escape(title)}", "", f"Report type: `{report_type}`", ""]
    if report_type == "executive_posture":
        parts.append(md_executive(selected, limit, ctx))
    elif report_type == "soc_triage":
        parts.append(md_soc(selected, limit, ctx))
    elif report_type == "control_review":
        parts.append(md_control(selected, limit, ctx))
    elif report_type == "scorecard_brief":
        parts.append(md_scorecard_brief(selected, ctx))
    elif report_type == "crawler_governance":
        parts.append(md_domain_report("Crawler Governance", selected, limit, ctx, crawler_features_for_card))
    elif report_type == "edge_ops_impact":
        parts.append(md_domain_report("Edge/Ops Impact", selected, limit, ctx, edge_ops_features_for_card))
    parts.append(md_analyst_notes(notes, all_artifacts, ctx))
    parts.append(md_evidence_limits(all_artifacts, ctx))
    if ctx.warnings:
        parts.append("## Warnings\n\n" + "\n".join(f"- {md_escape(warning)}" for warning in ctx.warnings))
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
        md_table(["Metric", "Current", "Baseline", "Delta", "Pct change", "Direction", "Confidence"], rows) if rows else "No metric deltas available.",
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
        parts.extend(["## Domain Score Matrix", md_domain_matrix(scorecards, limit, ctx)])
        parts.extend(["## Security Evidence Notes", md_feature_list(scorecards, {"security_evidence"}, None, limit, ctx)])
    return "\n\n".join(parts)


def md_control(selected: dict[str, Any], limit: int, ctx: ReportContext) -> str:
    control = selected["control"]
    rows = []
    for effect in limited_rows(control.get("target_effects", []), limit, "control effects", ctx):
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
    return "\n\n".join(
        [
            "## Control Review Summary",
            f"Target: `{md_escape(control.get('target', {}))}`",
            f"Windows: {md_escape(window_text(control))}",
            "## Before/After/Expected",
            md_table(["Metric", "Before", "After", "Expected", "Delta vs expected", "Pct change", "Status", "Confidence"], rows) if rows else "No target effects available.",
            "This is an effectiveness review. The artifact alone is not causal proof.",
        ]
    )


def md_scorecard_brief(selected: dict[str, Any], ctx: ReportContext) -> str:
    card = selected["scorecard"]
    parts = [
        "## Entity",
        md_table(
            ["Entity type", "Entity", "Score", "Band", "Primary domain", "Confidence"],
            [[card.get("entity_type"), card.get("entity"), card.get("score"), card.get("band"), card.get("primary_domain"), card.get("confidence")]],
        ),
        "## Domain Scores",
        md_table(["Domain", "Score"], [[domain, score] for domain, score in (card.get("domain_scores") or {}).items()]),
        "## Feature Evidence",
        md_feature_rows(card.get("features", [])) if card.get("features") else "No evaluated features crossed thresholds.",
        "## Not Evaluated Features",
        md_missing_rows(card.get("not_evaluated_features", [])) if card.get("not_evaluated_features") else "No missing feature inputs reported.",
    ]
    steps = card.get("recommended_next_steps")
    if isinstance(steps, list) and steps:
        parts.extend(["## Recommended Next Steps", "\n".join(f"- {md_escape(step)}" for step in steps)])
    return "\n\n".join(parts)


def md_domain_report(
    heading: str,
    selected: dict[str, Any],
    limit: int,
    ctx: ReportContext,
    feature_selector: Any,
) -> str:
    scorecards = selected.get("scorecards") or []
    relevant: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    missing_count = 0
    for card in ordered_scorecards(scorecards, selected.get("index")):
        features, missing = feature_selector(card)
        missing_count += len(missing)
        if features:
            relevant.append((card, features))
    if not relevant:
        ctx.warn(f"{heading} report has scorecards but no eligible evaluated relevant evidence.")
        return f"## {heading} Summary\n\nNo relevant {heading.lower()} evidence available. This is not evidence that posture is safe."
    limited = limited_rows(relevant, limit, f"{heading.lower()} entities", ctx)
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
        md_table(["Entity type", "Entity", "Score", "Relevant features", "Confidence"], rows),
        f"## {heading} Evidence",
    ]
    for card, features in limited:
        parts.append(f"### {md_escape(card.get('entity'))}\n\n" + md_feature_rows(features))
    if missing_count:
        ctx.warn(f"{heading} report found {missing_count} relevant missing feature inputs.")
    return "\n\n".join(parts)


def md_ranking(index: dict[str, Any], limit: int, ctx: ReportContext) -> str:
    ranked = limited_rows(index.get("ranked_entities", []), limit, "ranked entities", ctx)
    rows = [
        [row.get("rank"), row.get("entity_type"), row.get("entity"), row.get("score"), row.get("band"), row.get("primary_domain"), row.get("confidence")]
        for row in ranked
    ]
    return md_table(["Rank", "Entity type", "Entity", "Score", "Band", "Primary domain", "Confidence"], rows) if rows else "No ranked entities available."


def md_movers(mover: dict[str, Any], limit: int, ctx: ReportContext) -> str:
    movers = limited_rows(mover.get("movers", []), limit, "movers", ctx)
    rows = [
        [row.get("value"), row.get("metric"), row.get("current"), row.get("baseline"), row.get("absolute_delta"), row.get("contribution_pct"), row.get("confidence")]
        for row in movers
    ]
    return md_table(["Value", "Metric", "Current", "Baseline", "Delta", "Contribution pct", "Confidence"], rows) if rows else "No mover attribution available."


def md_domain_matrix(scorecards: list[dict[str, Any]], limit: int, ctx: ReportContext) -> str:
    rows = []
    for card in limited_rows(scorecards, limit, "scorecards", ctx):
        domain_scores = card.get("domain_scores") or {}
        rows.append([card.get("entity"), card.get("score")] + [domain_scores.get(domain, "unavailable") for domain in sorted(domain_scores)])
    domains = sorted((scorecards[0].get("domain_scores") or {}).keys()) if scorecards else []
    return md_table(["Entity", "Total score"] + domains, rows) if rows else "No scorecard domain scores available."


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
            if feature.get("domain") in domains and (names is None or feature.get("name") in names)
        ]
        if features:
            selected.append((card, features))
    if not selected:
        return "No matching feature evidence available."
    lines = []
    for card, features in limited_rows(selected, limit, "feature evidence groups", ctx):
        lines.append(f"### {md_escape(card.get('entity'))}\n\n{md_feature_rows(features)}")
    return "\n\n".join(lines)


def md_feature_rows(features: list[dict[str, Any]]) -> str:
    rows = [
        [feature.get("domain"), feature.get("name"), feature.get("points"), feature.get("evidence")]
        for feature in features
    ]
    return md_table(["Domain", "Feature", "Points", "Evidence"], rows)


def md_missing_rows(missing: list[dict[str, Any]]) -> str:
    rows = [
        [feature.get("domain"), feature.get("name"), ", ".join(str(item) for item in feature.get("missing_inputs", [])), feature.get("reason")]
        for feature in missing
    ]
    return md_table(["Domain", "Feature", "Missing inputs", "Reason"], rows)


def ordered_scorecards(scorecards: list[dict[str, Any]], index: dict[str, Any] | None) -> list[dict[str, Any]]:
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


def crawler_features_for_card(card: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    features: list[dict[str, Any]] = []
    for feature in card.get("features", []):
        if feature.get("domain") != "crawler_governance":
            continue
        name = str(feature.get("name"))
        if name not in CRAWLER_FEATURES:
            continue
        if name in GENERIC_CRAWLER_RATE_FEATURES and not crawler_specific_provenance(card, feature):
            continue
        features.append(feature)
    missing = [
        item
        for item in card.get("not_evaluated_features", [])
        if item.get("domain") == "crawler_governance" and item.get("name") in CRAWLER_FEATURES
    ]
    return features, missing


def edge_ops_features_for_card(card: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    features = [
        feature
        for feature in card.get("features", [])
        if feature.get("domain") in EDGE_OPS_DOMAINS and feature.get("name") in EDGE_OPS_FEATURES
    ]
    missing = [
        item
        for item in card.get("not_evaluated_features", [])
        if item.get("domain") in EDGE_OPS_DOMAINS and item.get("name") in EDGE_OPS_FEATURES
    ]
    return features, missing


def md_evidence_limits(artifacts: list[dict[str, Any]], ctx: ReportContext) -> str:
    rows = []
    for artifact in artifacts:
        rows.append(
            [
                artifact.get("artifact_id"),
                artifact.get("schema_version"),
                artifact.get("table_used", "unavailable"),
                artifact.get("confidence", "unavailable"),
                ", ".join(str(reason) for reason in artifact.get("confidence_reasons", [])),
            ]
        )
    return "\n\n".join(
        [
            "## Evidence Limits",
            md_table(["Artifact ID", "Schema", "Table", "Confidence", "Confidence reasons"], rows),
            "Reports use emitted artifact fields only. Missing evidence is unavailable, not zero or safe.",
        ]
    )


def json_pointer_get(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise KeyError(pointer)
    current = value
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            raise KeyError(pointer)
    return current


def validate_analyst_notes(notes: list[dict[str, Any]], artifacts: list[dict[str, Any]]) -> None:
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
                raise ReportError(f"Analyst note {note_id} data_sources entries must be objects.")
            resolve_citation(source, artifacts)


def resolve_citation(
    source: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> tuple[dict[str, Any], Any]:
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
        candidates = [artifact for artifact in artifacts if artifact.get("artifact_id") == artifact_id]
        if not candidates:
            raise ReportError(f"Analyst-note citation artifact_id {artifact_id} cannot be resolved.")
        artifact = candidates[0]
        if schema and artifact.get("schema_version") != schema:
            raise ReportError(f"Analyst-note citation {artifact_id} schema mismatch: expected {schema}.")
    elif schema:
        candidates = [artifact for artifact in artifacts if artifact.get("schema_version") == schema]
        if len(candidates) != 1:
            raise ReportError(f"Analyst-note schema-only citation {schema} is ambiguous or missing.")
        artifact = candidates[0]
    else:
        raise ReportError("Analyst-note citation requires artifact_id or schema_version.")

    pointer = source.get("json_pointer")
    if not isinstance(pointer, str):
        raise ReportError("Analyst-note citation is missing json_pointer.")
    try:
        return artifact, json_pointer_get(artifact, pointer)
    except (KeyError, IndexError, ValueError, TypeError):
        raise ReportError(f"Analyst-note citation pointer {pointer} cannot be resolved.")


def md_analyst_notes(notes: list[dict[str, Any]], artifacts: list[dict[str, Any]], ctx: ReportContext) -> str:
    if not notes:
        return ""
    parts = [
        "## Analyst Notes",
        "These notes are interpretive narrative, not facts strictly proven by artifact data alone.",
    ]
    for index, note in enumerate(notes, start=1):
        author = note.get("author_type")
        if author not in {"llm", "analyst"}:
            ctx.warn(f"Analyst note {note.get('note_id', index)} has unsupported author_type {author}.")
            author = "analyst"
        label = "LLM interpretation" if author == "llm" else "Analyst interpretation"
        title = note.get("title") or f"Note {index}"
        parts.append(f"### {md_escape(title)}\n\n_{label}._ {md_escape(note.get('text', ''))}")
        sources = note.get("data_sources")
        if not isinstance(sources, list) or not sources:
            ctx.warn(f"Analyst note {note.get('note_id', index)} has no cited data sources.")
            continue
        citations = []
        for source in sources:
            artifact, resolved = resolve_citation(source, artifacts)
            artifact_label = artifact.get("artifact_id")
            citations.append(
                f"- {md_escape(source.get('label', source.get('json_pointer', 'citation')))}: "
                f"`{md_escape(artifact_label)}` {md_escape(source.get('json_pointer', ''))} = {md_escape(resolved)}"
            )
        if citations:
            parts.append("\n".join(citations))
    return "\n\n".join(parts)


def html_bar_svg(rows: list[tuple[str, float]], *, width: int = 520, row_height: int = 28) -> str:
    if not rows:
        return ""
    max_value = max((abs(value) for _, value in rows), default=1) or 1
    height = max(36, len(rows) * row_height + 16)
    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Bar chart">',
    ]
    label_width = 180
    bar_width = width - label_width - 56
    for index, (label, value) in enumerate(rows):
        y = 12 + index * row_height
        scaled = int(abs(value) / max_value * bar_width)
        parts.append(f'<text x="0" y="{y + 14}">{h_escape(label)}</text>')
        parts.append(f'<rect x="{label_width}" y="{y}" width="{scaled}" height="18" rx="2"></rect>')
        parts.append(f'<text x="{label_width + scaled + 8}" y="{y + 14}">{h_escape(value)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def render_html(
    title: str,
    report_type: str,
    selected: dict[str, Any],
    all_artifacts: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    limit: int,
    ctx: ReportContext,
) -> str:
    markdown = render_markdown(title, report_type, selected, all_artifacts, notes, limit, ctx)
    body = markdown_to_simple_html(markdown)
    chart = html_chart_for_report(report_type, selected, limit)
    css = """
body{font-family:Arial,sans-serif;margin:0;color:#17202a;background:#f7f8fa}
main{max-width:1120px;margin:0 auto;padding:32px}
h1{font-size:34px;margin:0 0 8px}h2{margin-top:32px;border-top:1px solid #d9dee7;padding-top:20px}
table{border-collapse:collapse;width:100%;margin:12px 0;background:#fff}th,td{border:1px solid #d8dee8;padding:8px;text-align:left;vertical-align:top}
th{background:#eef2f7}code{background:#eef2f7;padding:2px 4px;border-radius:3px}
.chart{width:100%;max-width:720px;background:#fff;border:1px solid #d8dee8;margin:16px 0}.chart rect{fill:#2474a6}.chart text{font-size:12px;fill:#17202a}
""".strip()
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{h_escape(title)}</title><style>{css}</style></head><body><main>"
        + chart
        + body
        + "</main></body></html>\n"
    )


def html_chart_for_report(report_type: str, selected: dict[str, Any], limit: int) -> str:
    if report_type == "executive_posture":
        metrics = selected["posture"].get("metrics", [])[:limit]
        rows = [(str(metric.get("name")), float(metric.get("current") or 0)) for metric in metrics]
        return html_bar_svg(rows)
    if report_type == "soc_triage":
        ranked = selected["index"].get("ranked_entities", [])[:limit]
        rows = [(str(row.get("entity")), float(row.get("score") or 0)) for row in ranked]
        return html_bar_svg(rows)
    if report_type == "scorecard_brief":
        scores = selected["scorecard"].get("domain_scores", {})
        rows = [(str(name), float(value or 0)) for name, value in scores.items()]
        return html_bar_svg(rows)
    if report_type == "control_review":
        effects = selected["control"].get("target_effects", [])[:limit]
        rows = [(str(row.get("metric")), float(row.get("after") or 0)) for row in effects]
        return html_bar_svg(rows)
    return ""


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
            output.append(f"<h1>{h_escape(line[2:])}</h1>")
        elif line.startswith("## "):
            close_list()
            output.append(f"<h2>{h_escape(line[3:])}</h2>")
        elif line.startswith("### "):
            close_list()
            output.append(f"<h3>{h_escape(line[4:])}</h3>")
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
    escaped = h_escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"_([^_]+)_", r"<em>\1</em>", escaped)
    return escaped


def table_to_html(lines: list[str]) -> str:
    rows = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and all(set(cell) <= {"-"} for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        return ""
    header = rows[0]
    body = rows[1:]
    output = ["<table><thead><tr>"]
    output.extend(f"<th>{h_escape(cell)}</th>" for cell in header)
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
    artifacts, notes, wrapper_report_type, wrapper_title, wrapper_limit, scope_label, raw_mode = load_report_input(value, args, ctx)
    report_type, title, limit, _ = resolve_options(
        artifacts,
        wrapper_report_type=wrapper_report_type,
        wrapper_title=wrapper_title,
        wrapper_limit=wrapper_limit,
        scope_label=scope_label,
        raw_mode=raw_mode,
        args=args,
        ctx=ctx,
    )
    selected = validate_report_artifacts(report_type, artifacts, ctx)
    scan_metadata_warnings(artifacts, ctx)
    validate_analyst_notes(notes, artifacts)
    if args.format == "html":
        return render_html(title, report_type, selected, artifacts, notes, limit, ctx), ctx.warnings
    return render_markdown(title, report_type, selected, artifacts, notes, limit, ctx), ctx.warnings


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
