#!/usr/bin/env python3
"""Capture Hydrolix query JSON to disk without printing result rows."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TIME_PREDICATE_RE = re.compile(
    r"\b(?:timestamp|reqTimeSec)\b\s*(?:=|!=|<>|>=|<=|>|<|BETWEEN|IN)(?:\s|\(|'|$)",
    re.IGNORECASE,
)
DEFAULT_MUX_PROJECT = Path.home() / "src/mcp-hydrolix-mux"
FORMAT_RE = re.compile(r"\bFORMAT\s+\w+\b", re.IGNORECASE)

PRESET_CHOICES = (
    "posture-overview",
    "posture-by-asn",
    "posture-by-path",
    "siem-policy",
)


def read_sql(args: argparse.Namespace) -> str:
    if args.preset:
        if args.sql or args.sql_file:
            raise SystemExit("Use either --preset or --sql/--sql-file, not both.")
        return render_preset_sql(args)
    if args.sql and args.sql_file:
        raise SystemExit("Use either --sql or --sql-file, not both.")
    if args.sql:
        sql = args.sql
    elif args.sql_file:
        sql = Path(args.sql_file).read_text()
    elif not sys.stdin.isatty():
        sql = sys.stdin.read()
    else:
        raise SystemExit("Provide SQL with --sql, --sql-file, or stdin.")
    sql = sql.strip()
    if not sql:
        raise SystemExit("SQL is empty.")
    return apply_time_window_to_sql(sql, args)


def ensure_format_json(sql: str) -> str:
    if FORMAT_RE.search(sql.rstrip()):
        return sql
    return f"{sql.rstrip(';')} FORMAT JSON"


def parse_time(value: str, *, label: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SystemExit(f"--{label} must be an ISO-8601 timestamp with timezone.") from exc
    if parsed.tzinfo is None:
        raise SystemExit(f"--{label} must include a timezone, for example 2026-05-08T00:00:00Z.")
    return parsed.astimezone(timezone.utc)


def sql_timestamp(value: datetime) -> str:
    text = value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return f"toDateTime('{text}', 'UTC')"


def duration_minutes(start: datetime, end: datetime) -> float:
    seconds = (end - start).total_seconds()
    if seconds <= 0:
        raise SystemExit("--end must be later than --start.")
    return seconds / 60


def selected_granularity(start: datetime, end: datetime, requested: str, *, surface: str) -> str:
    if requested != "auto":
        return requested
    minutes = duration_minutes(start, end)
    if minutes < 180:
        return "minute"
    if minutes < 2880:
        return "hour"
    return "day"


def selected_table(database: str, surface: str, granularity: str) -> str:
    if surface == "posture":
        return f"{database}.bi_summary_{granularity}"
    if surface == "siem-policy":
        return f"{database}.bi_siem_policy_summary_{granularity}"
    raise AssertionError(surface)


def selected_time_column(surface: str) -> str:
    if surface == "posture":
        return "reqTimeSec"
    if surface == "siem-policy":
        return "timestamp"
    raise AssertionError(surface)


def require_time_window(args: argparse.Namespace) -> tuple[datetime, datetime]:
    if not args.start or not args.end:
        raise SystemExit("--start and --end are required for Bot Insights capture presets.")
    start = parse_time(args.start, label="start")
    end = parse_time(args.end, label="end")
    duration_minutes(start, end)
    return start, end


def time_context(args: argparse.Namespace) -> dict[str, str]:
    if not args.start or not args.end:
        return {}
    start = parse_time(args.start, label="start")
    end = parse_time(args.end, label="end")
    duration_minutes(start, end)
    surface = args.table_surface
    if surface == "auto":
        surface = "siem-policy" if args.preset and args.preset.startswith("siem-") else "posture"
    time_column = args.time_column
    if time_column == "auto":
        time_column = selected_time_column(surface)
    granularity = selected_granularity(start, end, args.granularity, surface=surface)
    table = selected_table(args.database, surface, granularity)
    time_filter = f"{time_column} >= {sql_timestamp(start)} AND {time_column} < {sql_timestamp(end)}"
    return {
        "start": sql_timestamp(start),
        "end": sql_timestamp(end),
        "database": args.database,
        "table": table,
        "time_column": time_column,
        "time_filter": time_filter,
        "granularity": granularity,
        "surface": surface,
    }


def apply_time_window_to_sql(sql: str, args: argparse.Namespace) -> str:
    context = time_context(args)
    if context:
        for key, value in context.items():
            sql = sql.replace(f"{{{{{key}}}}}", value)
    if "{{" in sql or "}}" in sql:
        raise SystemExit("SQL contains unresolved {{...}} placeholders.")
    if args.require_time_range and not TIME_PREDICATE_RE.search(sql):
        raise SystemExit(
            "SQL must include a timestamp/reqTimeSec predicate, or use --start/--end "
            "with {{time_filter}} in the SQL."
        )
    return sql


def render_preset_sql(args: argparse.Namespace) -> str:
    start, end = require_time_window(args)
    if args.preset.startswith("siem-"):
        surface = "siem-policy"
    else:
        surface = "posture"
    granularity = selected_granularity(start, end, args.granularity, surface=surface)
    table = selected_table(args.database, surface, granularity)
    time_column = selected_time_column(surface)
    time_filter = f"{time_column} >= {sql_timestamp(start)} AND {time_column} < {sql_timestamp(end)}"
    limit = args.limit

    if args.preset == "posture-overview":
        return f"""
SELECT
  trafficCohort,
  aiCategory,
  userAgentCategory,
  countMerge(`count()`) AS requests,
  countMergeIf(`count()`, cacheStatus = false) AS cache_misses,
  countMergeIf(`count()`, statusCode = 429) AS rate_limited_requests,
  countMergeIf(`count()`, statusCode >= 500) AS error_5xx_requests,
  round(
    sumIfMerge(`sumIf(Origin_TurnAroundTime, and(isNotNull(Origin_TurnAroundTime), greaterOrEquals(Origin_TurnAroundTime, 0)))`)
    / nullIf(countIfMerge(`countIf(and(isNotNull(Origin_TurnAroundTime), greaterOrEquals(Origin_TurnAroundTime, 0)))`), 0),
    2
  ) AS avg_origin_tat_ms
FROM {table}
WHERE {time_filter}
GROUP BY trafficCohort, aiCategory, userAgentCategory
ORDER BY requests DESC
LIMIT {limit}
""".strip()

    if args.preset == "posture-by-asn":
        return f"""
SELECT
  asn AS client_asn,
  reqHost AS request_host,
  trafficCohort,
  countMerge(`count()`) AS requests,
  countMergeIf(`count()`, cacheStatus = false) AS cache_misses,
  countMergeIf(`count()`, statusCode = 429) AS rate_limited_requests,
  countMergeIf(`count()`, statusCode >= 500) AS error_5xx_requests
FROM {table}
WHERE {time_filter}
GROUP BY client_asn, request_host, trafficCohort
ORDER BY requests DESC
LIMIT {limit}
""".strip()

    if args.preset == "posture-by-path":
        return f"""
SELECT
  requestPathPattern AS request_path_pattern,
  reqHost AS request_host,
  trafficCohort,
  resourceCategory,
  countMerge(`count()`) AS requests,
  countMergeIf(`count()`, cacheStatus = false) AS cache_misses,
  countMergeIf(`count()`, statusCode = 429) AS rate_limited_requests,
  countMergeIf(`count()`, statusCode >= 500) AS error_5xx_requests
FROM {table}
WHERE {time_filter}
GROUP BY request_path_pattern, request_host, trafficCohort, resourceCategory
ORDER BY requests DESC
LIMIT {limit}
""".strip()

    if args.preset == "siem-policy":
        return f"""
SELECT
  policyId,
  actionClass,
  botType,
  host AS request_host,
  asn AS client_asn,
  countMerge(`count()`) AS requests,
  countIfMerge(`countIf(equals(actionClass, 'deny'))`) AS blocked_requests,
  countIfMerge(`countIf(equals(authOutcome, 'fail'))`) AS auth_fail_requests,
  avgIfMerge(`avgIf(botScore, greater(botScore, 0))`) AS avg_bot_score,
  uniqMerge(`uniq(clientIP)`) AS unique_client_ips
FROM {table}
WHERE {time_filter}
GROUP BY policyId, actionClass, botType, request_host, client_asn
ORDER BY blocked_requests DESC, auth_fail_requests DESC, requests DESC
LIMIT {limit}
""".strip()

    raise AssertionError(args.preset)


def mux_export_command() -> list[str]:
    mux_project = Path(os.environ.get("HYDROLIX_MUX_PROJECT") or DEFAULT_MUX_PROJECT).expanduser()
    if (mux_project / "pyproject.toml").exists():
        uv = shutil.which("uv")
        if uv:
            return [
                uv,
                "run",
                "--project",
                str(mux_project),
                "mcp-hydrolix-mux",
                "export-select-query",
            ]
    binary = shutil.which("mcp-hydrolix-mux")
    if binary:
        return [binary, "export-select-query"]
    raise SystemExit(
        "Could not find mcp-hydrolix-mux. Set HYDROLIX_MUX_PROJECT to the standalone "
        "mcp-hydrolix-mux checkout, or install the mcp-hydrolix-mux console script."
    )


def run_mux_export(cluster: str, sql: str, output: Path) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sql", delete=False) as handle:
        sql_file = Path(handle.name)
        handle.write(sql)
        handle.write("\n")
    command = [
        *mux_export_command(),
        "--cluster",
        cluster,
        "--query-file",
        str(sql_file),
        "--output",
        str(output),
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
    finally:
        sql_file.unlink(missing_ok=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise SystemExit(f"mcp-hydrolix-mux export-select-query failed: {detail}")
    try:
        summary = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit("mcp-hydrolix-mux did not return a JSON metadata summary.") from exc
    if not isinstance(summary, dict):
        raise SystemExit("mcp-hydrolix-mux metadata summary was not a JSON object.")
    return summary


def load_response_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Mux export file was not valid JSON: {exc}") from exc


def shape_output(response: Any, shape: str) -> Any:
    if shape == "clickhouse":
        return response
    if shape == "rows":
        if isinstance(response, dict):
            if isinstance(response.get("data"), list):
                return response["data"]
            if isinstance(response.get("rows"), list):
                return response["rows"]
        if isinstance(response, list):
            return response
        raise SystemExit("Cannot shape mux export as rows: JSON has no data or rows array.")
    raise AssertionError(shape)


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        tmp_path = Path(handle.name)
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a Hydrolix query and write JSON directly to a local file."
    )
    parser.add_argument("--cluster", required=True, help="Hydrolix cluster alias.")
    parser.add_argument(
        "--preset",
        choices=PRESET_CHOICES,
        help="Use a vetted Bot Insights summary-table query preset.",
    )
    parser.add_argument(
        "--start",
        help="Inclusive ISO-8601 UTC start timestamp, for example 2026-05-01T00:00:00Z.",
    )
    parser.add_argument(
        "--end",
        help="Exclusive ISO-8601 UTC end timestamp, for example 2026-05-08T00:00:00Z.",
    )
    parser.add_argument(
        "--database",
        default="akamai",
        help="Hydrolix database/project name for Bot Insights summary presets.",
    )
    parser.add_argument(
        "--granularity",
        choices=("auto", "minute", "hour", "day"),
        default="auto",
        help="Summary-table granularity. Auto uses <3h minute, <48h hour, else day.",
    )
    parser.add_argument(
        "--table-surface",
        choices=("auto", "posture", "siem-policy"),
        default="auto",
        help="Table family used for SQL placeholders in custom SQL.",
    )
    parser.add_argument(
        "--time-column",
        choices=("auto", "timestamp", "reqTimeSec"),
        default="auto",
        help="Time column used for SQL placeholders in custom SQL.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="LIMIT used by query presets.",
    )
    parser.add_argument("--sql", help="SQL text. Use --sql-file for larger queries.")
    parser.add_argument("--sql-file", help="Path to a SQL file.")
    parser.add_argument("--output", required=True, help="Output JSON path.")
    parser.add_argument(
        "--shape",
        choices=("clickhouse", "rows"),
        default="clickhouse",
        help="Write the full ClickHouse JSON response or only the data row array.",
    )
    parser.add_argument(
        "--no-format-json",
        action="store_true",
        help="Compatibility no-op. The MCP query path controls result formatting.",
    )
    parser.add_argument(
        "--no-require-time-range",
        dest="require_time_range",
        action="store_false",
        help="Allow custom SQL without an explicit timestamp or reqTimeSec predicate.",
    )
    parser.set_defaults(require_time_range=True)
    args = parser.parse_args()
    if args.limit <= 0:
        raise SystemExit("--limit must be positive.")
    return args


def main() -> int:
    args = parse_args()
    sql = read_sql(args)

    output_path = Path(args.output)
    if args.shape == "clickhouse":
        mux_summary = run_mux_export(args.cluster, sql, output_path)
        shaped_rows = None
    else:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
            raw_output = Path(handle.name)
        try:
            mux_summary = run_mux_export(args.cluster, sql, raw_output)
            shaped = shape_output(load_response_file(raw_output), args.shape)
            write_json_atomic(output_path, shaped)
            shaped_rows = len(shaped) if isinstance(shaped, list) else None
        finally:
            raw_output.unlink(missing_ok=True)

    print(
        json.dumps(
            {
                "cluster": args.cluster,
                "mux_cluster": mux_summary.get("cluster"),
                "preset": args.preset,
                "output": str(output_path),
                "shape": args.shape,
                "rows": shaped_rows,
                "mux_bytes": mux_summary.get("bytes"),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
