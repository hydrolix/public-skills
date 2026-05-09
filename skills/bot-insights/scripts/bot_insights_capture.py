#!/usr/bin/env python3
"""Capture vetted Bot Insights Hydrolix query JSON to disk."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TIME_PREDICATE_RE = re.compile(
    r"\b(?:timestamp|reqTimeSec)\b\s*(?:=|!=|<>|>=|<=|>|<|BETWEEN|IN)(?:\s|\(|'|$)",
    re.IGNORECASE,
)
FORMAT_RE = re.compile(r"\bFORMAT\s+\w+\b", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"\{\{[^}]+\}\}|\$\{[^}]+\}")
SENTINEL_ENV = "BOT_INSIGHTS_CAPTURE_OP_RUN"
CLUSTER_DIR_ENV = ("BOT_INSIGHTS_CLUSTER_DIR", "HYDROLIX_CLUSTER_DIR", "HDX_CLUSTER_DIR")
NEEDS_MCP_EXIT = 42
HANDOFF_SCHEMA = "bot_hydrolix_mcp_query_request.v1"
PRESET_CHOICES = (
    "posture-overview",
    "posture-by-asn",
    "posture-by-path",
    "siem-policy",
)


@dataclass(frozen=True)
class QueryConfig:
    url: str
    headers: dict[str, str]
    verify_tls: bool
    auth_mode: str


@dataclass(frozen=True)
class CredentialState:
    configured: bool
    host: str | None
    auth_mode: str | None
    missing: tuple[str, ...]
    unresolved_op: tuple[str, ...]
    env_file: str | None
    op_resolution: str


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        value = raw_value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def cluster_env_dir(env: dict[str, str] | None = None) -> Path:
    source = env or os.environ
    for key in CLUSTER_DIR_ENV:
        if source.get(key):
            return Path(source[key]).expanduser()
    return Path.home() / ".config/hydrolix/clusters"


def cluster_env_path(alias: str, env: dict[str, str] | None = None) -> Path:
    path = Path(alias).expanduser()
    if path.suffix == ".env" or path.is_absolute() or "/" in alias:
        return path
    return cluster_env_dir(env) / f"{alias}.env"


def file_may_need_op(path: Path) -> bool:
    return path.exists() and "op://" in path.read_text(encoding="utf-8")


def should_reexec_with_op(path: Path, env: dict[str, str] | None = None) -> bool:
    source = env or os.environ
    return (
        path.exists()
        and source.get(SENTINEL_ENV) != "1"
        and file_may_need_op(path)
        and shutil.which("op") is not None
    )


def reexec_with_op(path: Path) -> None:
    env = dict(os.environ)
    env[SENTINEL_ENV] = "1"
    command = [
        "op",
        "run",
        "--env-file",
        str(path),
        "--",
        sys.executable,
        str(Path(__file__).resolve()),
        *sys.argv[1:],
    ]
    os.execvpe("op", command, env)


def resolved_cluster_env_path(cluster: str | None) -> Path | None:
    if not cluster:
        return None
    env_path = cluster_env_path(cluster)
    if env_path.exists():
        return env_path
    if "/" in cluster or cluster.endswith(".env"):
        raise SystemExit(f"Cluster env file does not exist: {env_path}")
    return None


def merged_environment(cluster: str | None) -> tuple[dict[str, str], Path | None]:
    env_file_values: dict[str, str] = {}
    env_path = resolved_cluster_env_path(cluster)
    if env_path:
        if should_reexec_with_op(env_path):
            reexec_with_op(env_path)
        env_file_values = parse_env_file(env_path)

    merged = dict(env_file_values)
    merged.update(os.environ)
    return merged, env_path


def first_env(env: dict[str, str], *names: str) -> str | None:
    for name in names:
        value = env.get(name)
        if value:
            return value
    return None


def is_unresolved_secret(value: str | None) -> bool:
    return bool(value and value.strip().startswith("op://"))


def secret_error(name: str) -> SystemExit:
    return SystemExit(
        f"{name} is an unresolved op:// reference. Install/sign in to 1Password CLI "
        "or provide literal credentials in the current environment."
    )


def credential_state(env: dict[str, str], env_path: Path | None = None) -> CredentialState:
    host = first_env(env, "HYDROLIX_HOST", "HDX_HOSTNAME")
    token = first_env(env, "HYDROLIX_TOKEN", "HDX_TOKEN")
    user = first_env(env, "HYDROLIX_USER", "HDX_USERNAME")
    password = first_env(env, "HYDROLIX_PASSWORD", "HDX_PASSWORD")

    unresolved: list[str] = []
    for label, value in (
        ("HYDROLIX_HOST/HDX_HOSTNAME", host),
        ("HYDROLIX_TOKEN/HDX_TOKEN", token),
        ("HYDROLIX_USER/HDX_USERNAME", user),
        ("HYDROLIX_PASSWORD/HDX_PASSWORD", password),
    ):
        if is_unresolved_secret(value):
            unresolved.append(label)

    missing: list[str] = []
    if not host:
        missing.append("HYDROLIX_HOST/HDX_HOSTNAME")

    auth_mode: str | None = None
    if token:
        auth_mode = "bearer"
    elif user and password:
        auth_mode = "basic"
    else:
        missing.append(
            "HYDROLIX_TOKEN/HDX_TOKEN or HYDROLIX_USER/HYDROLIX_PASSWORD or HDX_USERNAME/HDX_PASSWORD"
        )

    configured = bool(host and auth_mode and not unresolved)
    if unresolved:
        configured = False

    op_resolution = "not_required"
    if unresolved:
        op_resolution = "unresolved"
    elif env_path and file_may_need_op(env_path):
        op_resolution = "resolved_by_op_run" if os.environ.get(SENTINEL_ENV) == "1" else "resolved"

    return CredentialState(
        configured=configured,
        host=host,
        auth_mode=auth_mode if configured else None,
        missing=tuple(missing),
        unresolved_op=tuple(unresolved),
        env_file=str(env_path) if env_path else None,
        op_resolution=op_resolution,
    )


def normalize_query_url(host: str, scheme: str = "https") -> str:
    cleaned = host.strip()
    if not cleaned:
        raise SystemExit("HYDROLIX_HOST or HDX_HOSTNAME is required.")
    if "://" not in cleaned:
        cleaned = f"{scheme}://{cleaned}"
    cleaned = cleaned.rstrip("/")
    if cleaned.endswith("/query"):
        return f"{cleaned}/"
    if cleaned.endswith("/query/"):
        return cleaned
    return f"{cleaned}/query/"


def bool_env(value: str | None) -> bool:
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on"})


def build_query_config(env: dict[str, str]) -> QueryConfig:
    host = first_env(env, "HYDROLIX_HOST", "HDX_HOSTNAME")
    if is_unresolved_secret(host):
        raise secret_error("HYDROLIX_HOST/HDX_HOSTNAME")
    if not host:
        raise SystemExit("HYDROLIX_HOST or HDX_HOSTNAME is required.")

    scheme = first_env(env, "HDX_SCHEME") or "https"
    url = normalize_query_url(host, scheme)
    headers = {"Content-Type": "text/plain; charset=utf-8", "Accept": "application/json"}

    token = first_env(env, "HYDROLIX_TOKEN", "HDX_TOKEN")
    if token:
        if is_unresolved_secret(token):
            raise secret_error("HYDROLIX_TOKEN/HDX_TOKEN")
        headers["Authorization"] = f"Bearer {token}"
        auth_mode = "bearer"
    else:
        user = first_env(env, "HYDROLIX_USER", "HDX_USERNAME")
        password = first_env(env, "HYDROLIX_PASSWORD", "HDX_PASSWORD")
        if is_unresolved_secret(user):
            raise secret_error("HYDROLIX_USER/HDX_USERNAME")
        if is_unresolved_secret(password):
            raise secret_error("HYDROLIX_PASSWORD/HDX_PASSWORD")
        if not user or not password:
            raise SystemExit(
                "Provide HYDROLIX_TOKEN/HDX_TOKEN or HYDROLIX_USER/HYDROLIX_PASSWORD "
                "(HDX_USERNAME/HDX_PASSWORD also accepted)."
            )
        encoded = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {encoded}"
        auth_mode = "basic"

    return QueryConfig(
        url=url,
        headers=headers,
        verify_tls=not bool_env(first_env(env, "HDX_INSECURE_TLS")),
        auth_mode=auth_mode,
    )


def ensure_format_json(sql: str) -> str:
    if FORMAT_RE.search(sql.rstrip()):
        return sql
    return f"{sql.rstrip(';')} FORMAT JSON"


def reject_invalid_sql(sql: str, *, require_time_range: bool) -> None:
    compact = sql.strip()
    if not compact:
        raise SystemExit("SQL is empty.")
    body = re.sub(r"\bFORMAT\s+\w+\s*$", "", compact, flags=re.IGNORECASE).strip()
    statements = [part.strip() for part in body.split(";") if part.strip()]
    if len(statements) > 1:
        raise SystemExit("SQL must contain exactly one SELECT statement.")
    if not re.match(r"^(?:WITH\b[\s\S]+?\bSELECT\b|SELECT\b)", statements[0], re.IGNORECASE):
        raise SystemExit("Only SELECT SQL is allowed.")
    if PLACEHOLDER_RE.search(compact):
        raise SystemExit("SQL contains unresolved placeholders.")
    if require_time_range and not TIME_PREDICATE_RE.search(compact):
        raise SystemExit("SQL must include a timestamp or reqTimeSec predicate.")


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


def selected_granularity(start: datetime, end: datetime, requested: str) -> str:
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


def read_sql(args: argparse.Namespace) -> str:
    if args.preset:
        if args.sql or args.sql_file:
            raise SystemExit("Use either --preset or --sql/--sql-file, not both.")
        sql = render_preset_sql(args)
    else:
        if args.sql and args.sql_file:
            raise SystemExit("Use either --sql or --sql-file, not both.")
        if args.sql:
            sql = args.sql
        elif args.sql_file:
            sql = Path(args.sql_file).read_text(encoding="utf-8")
        elif not sys.stdin.isatty():
            sql = sys.stdin.read()
        else:
            raise SystemExit("Provide SQL with --preset, --sql, --sql-file, or stdin.")
        sql = apply_time_window_to_sql(sql.strip(), args)
    sql = ensure_format_json(sql)
    reject_invalid_sql(sql, require_time_range=args.require_time_range)
    return sql


def time_context(args: argparse.Namespace) -> dict[str, str]:
    if not args.start or not args.end:
        return {}
    start = parse_time(args.start, label="start")
    end = parse_time(args.end, label="end")
    duration_minutes(start, end)
    surface = "siem-policy" if args.preset and args.preset.startswith("siem-") else "posture"
    time_column = selected_time_column(surface)
    granularity = selected_granularity(start, end, args.granularity)
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
    for key, value in context.items():
        sql = sql.replace(f"{{{{{key}}}}}", value)
    return sql


def render_preset_sql(args: argparse.Namespace) -> str:
    start, end = require_time_window(args)
    surface = "siem-policy" if args.preset.startswith("siem-") else "posture"
    granularity = selected_granularity(start, end, args.granularity)
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


def query_hydrolix(sql: str, config: QueryConfig) -> tuple[dict[str, Any], dict[str, Any]]:
    context = None
    if config.url.startswith("https://") and not config.verify_tls:
        context = ssl._create_unverified_context()
    request = urllib.request.Request(
        config.url,
        data=sql.encode("utf-8"),
        headers=config.headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, context=context) as response:
            body = response.read()
            headers = dict(response.headers.items())
            status = response.status
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise SystemExit(f"Hydrolix query failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Hydrolix query failed: {exc.reason}") from exc

    try:
        parsed = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit("Hydrolix query did not return valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("Hydrolix query JSON was not a ClickHouse object.")
    return parsed, {"status": status, "headers": headers, "response_bytes": len(body)}


def shape_output(response: Any, shape: str) -> Any:
    if shape == "clickhouse":
        return response
    if shape == "rows":
        if isinstance(response, dict) and isinstance(response.get("data"), list):
            return response["data"]
        if isinstance(response, dict) and isinstance(response.get("rows"), list):
            return response["rows"]
        if isinstance(response, list):
            return response
        raise SystemExit("Cannot shape Hydrolix response as rows: JSON has no data or rows array.")
    raise AssertionError(shape)


def write_json_atomic(path: Path, data: Any) -> int:
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
    return path.stat().st_size


def response_row_count(response: Any, shaped: Any) -> int | None:
    if isinstance(shaped, list):
        return len(shaped)
    if isinstance(response, dict):
        rows = response.get("rows")
        if isinstance(rows, int):
            return rows
        data = response.get("data")
        if isinstance(data, list):
            return len(data)
    return None


def extract_query_stats(meta: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    if isinstance(response.get("statistics"), dict):
        stats["statistics"] = response["statistics"]
    headers = meta.get("headers") or {}
    for key, value in headers.items():
        if key.lower() == "x-hdx-query-stats":
            try:
                stats["hdx_query_stats"] = json.loads(value)
            except json.JSONDecodeError:
                stats["hdx_query_stats"] = value
    return stats


def build_handoff_packet(
    args: argparse.Namespace,
    sql: str,
    credentials: CredentialState,
    output_path: Path,
) -> dict[str, Any]:
    report_context = {
        "preset": args.preset,
        "start": args.start,
        "end": args.end,
        "granularity": args.granularity,
        "limit": args.limit,
    }
    instruction = (
        "Run Hydrolix MCP run_select_query with the supplied cluster and validated_sql, "
        f"then save the complete JSON result to {output_path}."
    )
    packet: dict[str, Any] = {
        "schema_version": HANDOFF_SCHEMA,
        "cluster": args.cluster,
        "database": args.database,
        "preset": args.preset,
        "report_context": {key: value for key, value in report_context.items() if value is not None},
        "validated_sql": sql,
        "expected_output_shape": args.shape,
        "target_raw_output_path": str(output_path),
        "mcp": {
            "server": "hydrolix_mux",
            "tool": "run_select_query",
            "arguments": {
                "cluster": args.cluster,
                "query": sql,
            },
        },
        "instruction": instruction,
        "credential_status": {
            "configured": False,
            "missing": list(credentials.missing),
            "unresolved_op": list(credentials.unresolved_op),
            "env_file": credentials.env_file,
            "op_resolution": credentials.op_resolution,
        },
    }
    return packet


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture vetted Bot Insights Hydrolix query JSON or emit an MCP handoff."
    )
    parser.add_argument("--cluster", help="Hydrolix cluster alias or .env file path.")
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
    parser.add_argument("--limit", type=int, default=100, help="LIMIT used by query presets.")
    parser.add_argument("--sql", help="Guarded Bot Insights SQL text.")
    parser.add_argument("--sql-file", help="Path to a guarded Bot Insights SQL file.")
    parser.add_argument("--output", required=True, help="Output JSON path.")
    parser.add_argument(
        "--shape",
        choices=("clickhouse", "rows"),
        default="clickhouse",
        help="Write the full ClickHouse JSON response or only the data row array.",
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
    output_path = Path(args.output).expanduser().resolve()
    env, env_path = merged_environment(args.cluster)
    credentials = credential_state(env, env_path)
    if not credentials.configured:
        print(json.dumps(build_handoff_packet(args, sql, credentials, output_path), sort_keys=True))
        return NEEDS_MCP_EXIT

    config = build_query_config(env)
    response, meta = query_hydrolix(sql, config)
    shaped = shape_output(response, args.shape)
    bytes_written = write_json_atomic(output_path, shaped)
    rows = response_row_count(response, shaped)

    summary = {
        "auth_mode": config.auth_mode,
        "bytes_written": bytes_written,
        "cluster": args.cluster,
        "output": str(output_path),
        "preset": args.preset,
        "query_url": config.url,
        "rows": rows,
        "shape": args.shape,
        "verify_tls": config.verify_tls,
    }
    summary.update(extract_query_stats(meta, response))
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
