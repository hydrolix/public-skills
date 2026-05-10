from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


PUBLIC_SKILLS = Path("/Users/turtlebender/src/public-skills")
CAPTURE = Path(__file__).resolve().with_name("bot_insights_capture.py")
DEFAULT_SAMPLE_ROOT = Path("/Users/turtlebender/src/sample-data/bot-insights/1.1")
NEEDS_MCP_EXIT = 42
HANDOFF_SCHEMA = "bot_hydrolix_mcp_query_request.v1"


def parse_time(value: str, label: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SystemExit(
            f"--{label} must be ISO-8601, for example 2026-05-08T00:00:00Z"
        ) from exc
    if parsed.tzinfo is None:
        raise SystemExit(
            f"--{label} must include a timezone, for example 2026-05-08T00:00:00Z"
        )
    return parsed.astimezone(timezone.utc)


def sql_ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


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


def as_number(value):
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


def human_number(value, *, percent: bool = False, signed: bool = False) -> str:
    number = as_number(value)
    if number is None:
        return "unavailable" if value is None else str(value)
    sign = "+" if signed and number > 0 else ""
    if percent:
        return f"{sign}{number:.1f}%"
    abs_number = abs(number)
    if abs_number >= 1_000_000_000:
        return f"{sign}{number / 1_000_000_000:.2f}B"
    if abs_number >= 1_000_000:
        return f"{sign}{number / 1_000_000:.2f}M"
    if abs_number >= 1_000:
        return f"{sign}{number / 1_000:.2f}K"
    if number.is_integer():
        return f"{sign}{int(number):,}"
    return f"{sign}{number:,.2f}"


def pct(numerator, denominator):
    numerator = as_number(numerator)
    denominator = as_number(denominator)
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator * 100


def pct_change(current, baseline):
    current = as_number(current)
    baseline = as_number(baseline)
    if current is None or baseline is None:
        return None
    return (current - baseline) / max(baseline, 1) * 100


def label_change(value) -> str:
    number = as_number(value)
    if number is None:
        return "not evaluated"
    abs_number = abs(number)
    if abs_number < 1:
        return "flat"
    if abs_number < 10:
        return "minor increase" if number > 0 else "minor decrease"
    if abs_number < 50:
        return "moderate increase" if number > 0 else "moderate decrease"
    return "material increase" if number > 0 else "material decrease"


def choose_granularity(start: datetime, end: datetime) -> str:
    minutes = (end - start).total_seconds() / 60
    if minutes <= 0:
        raise SystemExit("--end must be later than --start")
    if minutes < 180:
        return "minute"
    if minutes < 2880:
        return "hour"
    return "day"


def sql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def bucket_expr(column: str, granularity: str) -> str:
    if granularity == "minute":
        return f"toStartOfMinute({column})"
    if granularity == "hour":
        return f"toStartOfHour({column})"
    return f"toStartOfDay({column})"


def run(
    cmd: list[str],
    *,
    stdout_path: Path | None = None,
    cwd: Path | None = None,
    allowed_returncodes: tuple[int, ...] = (),
) -> str:
    ok_codes = (0, *allowed_returncodes)
    if stdout_path is None:
        result = subprocess.run(
            cmd, cwd=cwd, text=True, capture_output=True, check=False
        )
        if result.returncode not in ok_codes:
            detail = result.stderr.strip() or result.stdout.strip()
            raise SystemExit(detail)
        return result.stdout
    with stdout_path.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            stdout=handle,
            stderr=subprocess.PIPE,
            check=False,
        )
    if result.returncode not in ok_codes:
        raise SystemExit(result.stderr.strip())
    return ""


def executive_posture_sql(
    database: str, start: datetime, end: datetime, baseline_start: datetime
) -> str:
    granularity = choose_granularity(start, end)
    table = f"{database}.bi_summary_{granularity}"
    return f"""
WITH
  toDateTime('{sql_ts(start)}', 'UTC') AS current_start,
  toDateTime('{sql_ts(end)}', 'UTC') AS current_end,
  toDateTime('{sql_ts(baseline_start)}', 'UTC') AS baseline_start
SELECT
  if(reqTimeSec >= current_start, 'current', 'baseline') AS period,
  countMerge(`count()`) AS requests,
  countMergeIf(`count()`, trafficCohort IN ('Bot', 'AI')) AS bot_like_requests,
  countMergeIf(`count()`, trafficCohort = 'AI') AS ai_requests,
  countMergeIf(`count()`, cacheStatus = false) AS cache_misses,
  countMergeIf(`count()`, statusCode = 429) AS rate_limited_requests,
  countMergeIf(`count()`, statusCode >= 500) AS error_5xx_requests
FROM {table}
WHERE reqTimeSec >= baseline_start
  AND reqTimeSec < current_end
GROUP BY period
ORDER BY period
""".strip()


def control_review_sql(
    database: str,
    start: datetime,
    end: datetime,
    baseline_start: datetime,
    policy_id: str | None = None,
    control_source: str = "siem-policy",
) -> str:
    granularity = choose_granularity(start, end)
    if control_source == "posture":
        table = f"{database}.bi_summary_{granularity}"
        return f"""
WITH
  toDateTime('{sql_ts(start)}', 'UTC') AS after_start,
  toDateTime('{sql_ts(end)}', 'UTC') AS after_end,
  toDateTime('{sql_ts(baseline_start)}', 'UTC') AS before_start
SELECT
  if(reqTimeSec >= after_start, 'after', 'before') AS period,
  countMerge(`count()`) AS requests,
  countMergeIf(`count()`, trafficCohort IN ('Bot', 'AI')) AS bot_like_requests,
  countMergeIf(`count()`, trafficCohort = 'AI') AS ai_requests,
  countMergeIf(`count()`, cacheStatus = false) AS cache_misses,
  countMergeIf(`count()`, statusCode = 429) AS rate_limited_requests,
  countMergeIf(`count()`, statusCode >= 500) AS error_5xx_requests
FROM {table}
WHERE reqTimeSec >= before_start
  AND reqTimeSec < after_end
GROUP BY period
ORDER BY period
""".strip()

    table = f"{database}.bi_siem_policy_summary_{granularity}"
    policy_filter = f"\n  AND policyId = {sql_literal(policy_id)}" if policy_id else ""
    return f"""
WITH
  toDateTime('{sql_ts(start)}', 'UTC') AS after_start,
  toDateTime('{sql_ts(end)}', 'UTC') AS after_end,
  toDateTime('{sql_ts(baseline_start)}', 'UTC') AS before_start
SELECT
  if(timestamp >= after_start, 'after', 'before') AS period,
  countMerge(`count()`) AS requests,
  countIfMerge(`countIf(equals(actionClass, 'deny'))`) AS siem_blocked_requests,
  countIfMerge(`countIf(equals(authOutcome, 'fail'))`) AS siem_auth_fail_requests,
  avgIfMerge(`avgIf(botScore, greater(botScore, 0))`) AS avg_bot_score,
  uniqMerge(`uniq(clientIP)`) AS unique_client_ips
FROM {table}
WHERE timestamp >= before_start
  AND timestamp < after_end{policy_filter}
GROUP BY period
ORDER BY period
""".strip()


def control_review_timeseries_sql(
    database: str,
    start: datetime,
    end: datetime,
    baseline_start: datetime,
    policy_id: str | None = None,
    control_source: str = "siem-policy",
) -> str:
    granularity = choose_granularity(start, end)
    if control_source == "posture":
        table = f"{database}.bi_summary_{granularity}"
        bucket = bucket_expr("reqTimeSec", granularity)
        return f"""
WITH
  toDateTime('{sql_ts(start)}', 'UTC') AS after_start,
  toDateTime('{sql_ts(end)}', 'UTC') AS after_end,
  toDateTime('{sql_ts(baseline_start)}', 'UTC') AS before_start
SELECT
  if(reqTimeSec >= after_start, 'after', 'before') AS period,
  {bucket} AS bucket,
  countMerge(`count()`) AS requests,
  countMergeIf(`count()`, trafficCohort IN ('Bot', 'AI')) AS bot_like_requests,
  countMergeIf(`count()`, trafficCohort = 'AI') AS ai_requests,
  countMergeIf(`count()`, cacheStatus = false) AS cache_misses,
  countMergeIf(`count()`, statusCode = 429) AS rate_limited_requests,
  countMergeIf(`count()`, statusCode >= 500) AS error_5xx_requests
FROM {table}
WHERE reqTimeSec >= before_start
  AND reqTimeSec < after_end
GROUP BY period, bucket
ORDER BY period, bucket
""".strip()

    table = f"{database}.bi_siem_policy_summary_{granularity}"
    bucket = bucket_expr("timestamp", granularity)
    policy_filter = f"\n  AND policyId = {sql_literal(policy_id)}" if policy_id else ""
    return f"""
WITH
  toDateTime('{sql_ts(start)}', 'UTC') AS after_start,
  toDateTime('{sql_ts(end)}', 'UTC') AS after_end,
  toDateTime('{sql_ts(baseline_start)}', 'UTC') AS before_start
SELECT
  if(timestamp >= after_start, 'after', 'before') AS period,
  {bucket} AS bucket,
  countMerge(`count()`) AS requests,
  countIfMerge(`countIf(equals(actionClass, 'deny'))`) AS siem_blocked_requests,
  countIfMerge(`countIf(equals(authOutcome, 'fail'))`) AS siem_auth_fail_requests,
  avgIfMerge(`avgIf(botScore, greater(botScore, 0))`) AS avg_bot_score,
  uniqMerge(`uniq(clientIP)`) AS unique_client_ips
FROM {table}
WHERE timestamp >= before_start
  AND timestamp < after_end{policy_filter}
GROUP BY period, bucket
ORDER BY period, bucket
""".strip()


SCORECARD_ENTITY_SQL = {
    "client_asn": "toString(asn)",
    "request_path_norm": "toString(requestPathPattern)",
    "request_host": "toString(reqHost)",
    "bot_class": "toString(userAgentCategory)",
    "ai_category": "toString(aiCategory)",
}

SOC_ENTITY_SQL = {
    "client_asn": "toString(asn)",
    "request_host": "toString(reqHost)",
    "bot_class": "toString(userAgentCategory)",
    "ai_category": "toString(aiCategory)",
}

CRAWLER_ENTITY_SQL = {
    "ai_category": "toString(aiCategory)",
    "bot_class": "toString(userAgentCategory)",
    "request_host": "toString(reqHost)",
}

CRAWLER_POPULATION_BY_ENTITY = {
    "ai_category": "ai_crawler",
    "bot_class": "crawler",
    "request_host": "crawler",
}

EDGE_OPS_ENTITY_SQL = {
    "client_asn": "toString(asn)",
    "request_host": "toString(reqHost)",
    "bot_class": "toString(userAgentCategory)",
}


def scorecard_sql(
    database: str,
    start: datetime,
    end: datetime,
    baseline_start: datetime,
    entity_type: str,
    producer_limit: int,
) -> str:
    granularity = choose_granularity(start, end)
    table = f"{database}.bi_summary_{granularity}"
    entity_expr = SCORECARD_ENTITY_SQL[entity_type]
    limit_clause = f"\nLIMIT {producer_limit}" if producer_limit > 0 else ""
    return f"""
WITH
  toDateTime('{sql_ts(start)}', 'UTC') AS current_start,
  toDateTime('{sql_ts(end)}', 'UTC') AS current_end,
  toDateTime('{sql_ts(baseline_start)}', 'UTC') AS baseline_start
SELECT
  {entity_expr} AS {entity_type},
  countMergeIf(`count()`, reqTimeSec >= current_start) AS current_requests,
  countMergeIf(`count()`, reqTimeSec < current_start) AS baseline_requests,
  if(current_requests > 0, countMergeIf(`count()`, reqTimeSec >= current_start AND trafficCohort IN ('Bot', 'AI')) / current_requests * 100, 0) AS current_bot_share_pct,
  if(baseline_requests > 0, countMergeIf(`count()`, reqTimeSec < current_start AND trafficCohort IN ('Bot', 'AI')) / baseline_requests * 100, 0) AS baseline_bot_share_pct,
  if(current_requests > 0, countMergeIf(`count()`, reqTimeSec >= current_start AND cacheStatus = false) / current_requests * 100, 0) AS current_cache_miss_pct,
  if(baseline_requests > 0, countMergeIf(`count()`, reqTimeSec < current_start AND cacheStatus = false) / baseline_requests * 100, 0) AS baseline_cache_miss_pct,
  if(current_requests > 0, countMergeIf(`count()`, reqTimeSec >= current_start AND statusCode = 429) / current_requests * 100, 0) AS current_rate_429_pct,
  if(baseline_requests > 0, countMergeIf(`count()`, reqTimeSec < current_start AND statusCode = 429) / baseline_requests * 100, 0) AS baseline_rate_429_pct,
  if(current_requests > 0, countMergeIf(`count()`, reqTimeSec >= current_start AND statusCode >= 500) / current_requests * 100, 0) AS current_rate_5xx_pct,
  if(baseline_requests > 0, countMergeIf(`count()`, reqTimeSec < current_start AND statusCode >= 500) / baseline_requests * 100, 0) AS baseline_rate_5xx_pct
FROM {table}
WHERE reqTimeSec >= baseline_start
  AND reqTimeSec < current_end
  AND {entity_expr} != ''
GROUP BY {entity_type}
ORDER BY current_requests DESC
{limit_clause}
""".strip()


def scorecard_soc_sql(
    database: str,
    start: datetime,
    end: datetime,
    baseline_start: datetime,
    entity_type: str,
    producer_limit: int,
) -> str:
    granularity = choose_granularity(start, end)
    table = f"{database}.bi_siem_policy_summary_{granularity}"
    if entity_type not in SOC_ENTITY_SQL:
        raise SystemExit(
            "--entity-type "
            + entity_type
            + " is not supported for soc_triage; use one of "
            + ", ".join(sorted(SOC_ENTITY_SQL))
        )
    entity_expr = SOC_ENTITY_SQL[entity_type]
    limit_clause = f"\nLIMIT {producer_limit}" if producer_limit > 0 else ""
    return f"""
WITH
  toDateTime('{sql_ts(start)}', 'UTC') AS current_start,
  toDateTime('{sql_ts(end)}', 'UTC') AS current_end,
  toDateTime('{sql_ts(baseline_start)}', 'UTC') AS baseline_start
SELECT
  {entity_expr} AS {entity_type},
  countMergeIf(`count()`, timestamp >= current_start AND timestamp < current_end) AS current_requests,
  countMergeIf(`count()`, timestamp >= baseline_start AND timestamp < current_start) AS baseline_requests,
  countIfMergeIf(
    `countIf(equals(actionClass, 'deny'))`,
    timestamp >= current_start AND timestamp < current_end
  ) AS siem_blocked_requests,
  countIfMergeIf(
    `countIf(equals(authOutcome, 'fail'))`,
    timestamp >= current_start AND timestamp < current_end
  ) AS siem_auth_fail_requests,
  avgIfMergeIf(
    `avgIf(botScore, greater(botScore, 0))`,
    timestamp >= current_start AND timestamp < current_end
  ) AS current_avg_bot_score,
  uniqMergeIf(`uniq(clientIP)`, timestamp >= current_start AND timestamp < current_end) AS current_unique_client_ips
FROM {table}
WHERE timestamp >= baseline_start
  AND timestamp < current_end
  AND {entity_expr} != ''
GROUP BY {entity_type}
HAVING current_requests > 0 OR siem_blocked_requests > 0 OR siem_auth_fail_requests > 0
ORDER BY siem_blocked_requests DESC, siem_auth_fail_requests DESC, current_requests DESC{limit_clause}
""".strip()


def scorecard_crawler_sql(
    database: str,
    start: datetime,
    end: datetime,
    baseline_start: datetime,
    entity_type: str,
    producer_limit: int,
) -> str:
    granularity = choose_granularity(start, end)
    table = f"{database}.bi_summary_{granularity}"
    if entity_type not in CRAWLER_ENTITY_SQL:
        raise SystemExit(
            "--entity-type "
            + entity_type
            + " is not supported for crawler_governance; use one of "
            + ", ".join(sorted(CRAWLER_ENTITY_SQL))
        )
    entity_expr = CRAWLER_ENTITY_SQL[entity_type]
    limit_clause = f"\nLIMIT {producer_limit}" if producer_limit > 0 else ""
    return f"""
WITH
  toDateTime('{sql_ts(start)}', 'UTC') AS current_start,
  toDateTime('{sql_ts(end)}', 'UTC') AS current_end,
  toDateTime('{sql_ts(baseline_start)}', 'UTC') AS baseline_start
SELECT
  {entity_expr} AS {entity_type},
  countMergeIf(`count()`, reqTimeSec >= current_start) AS current_requests,
  countMergeIf(`count()`, reqTimeSec < current_start) AS baseline_requests,
  if(current_requests > 0, countMergeIf(`count()`, reqTimeSec >= current_start AND statusCode = 429) / current_requests * 100, 0) AS current_rate_429_pct,
  if(baseline_requests > 0, countMergeIf(`count()`, reqTimeSec < current_start AND statusCode = 429) / baseline_requests * 100, 0) AS baseline_rate_429_pct,
  if(current_requests > 0, countMergeIf(`count()`, reqTimeSec >= current_start AND statusCode >= 500) / current_requests * 100, 0) AS current_rate_5xx_pct,
  if(baseline_requests > 0, countMergeIf(`count()`, reqTimeSec < current_start AND statusCode >= 500) / baseline_requests * 100, 0) AS baseline_rate_5xx_pct,
  countMergeIf(`count()`, reqTimeSec >= current_start AND trafficCohort = 'Bot' AND statusCode = 429) AS good_bot_429_requests,
  if(
    countMergeIf(`count()`, reqTimeSec >= current_start AND trafficCohort = 'Bot') > 0,
    countMergeIf(`count()`, reqTimeSec >= current_start AND trafficCohort = 'Bot' AND statusCode >= 400) /
      countMergeIf(`count()`, reqTimeSec >= current_start AND trafficCohort = 'Bot') * 100,
    0
  ) AS good_bot_error_rate_pct,
  toUInt64(0) AS policy_surface_failures,
  countMergeIf(`count()`, reqTimeSec >= current_start AND trafficCohort = 'AI') AS current_ai_crawler_requests,
  countMergeIf(`count()`, reqTimeSec < current_start AND trafficCohort = 'AI') AS baseline_ai_crawler_requests
FROM {table}
WHERE reqTimeSec >= baseline_start
  AND reqTimeSec < current_end
  AND {entity_expr} != ''
GROUP BY {entity_type}
ORDER BY current_requests DESC
{limit_clause}
""".strip()


def scorecard_edge_ops_sql(
    database: str,
    start: datetime,
    end: datetime,
    baseline_start: datetime,
    entity_type: str,
    producer_limit: int,
) -> str:
    granularity = choose_granularity(start, end)
    table = f"{database}.bi_summary_{granularity}"
    if entity_type not in EDGE_OPS_ENTITY_SQL:
        raise SystemExit(
            "--entity-type "
            + entity_type
            + " is not supported for edge_ops_impact; use one of "
            + ", ".join(sorted(EDGE_OPS_ENTITY_SQL))
        )
    entity_expr = EDGE_OPS_ENTITY_SQL[entity_type]
    limit_clause = f"\nLIMIT {producer_limit}" if producer_limit > 0 else ""
    return f"""
WITH
  toDateTime('{sql_ts(start)}', 'UTC') AS current_start,
  toDateTime('{sql_ts(end)}', 'UTC') AS current_end,
  toDateTime('{sql_ts(baseline_start)}', 'UTC') AS baseline_start,
  cluster_total AS (
    SELECT
      countMergeIf(`count()`, reqTimeSec >= current_start) * 1.0 AS cluster_requests
    FROM {table}
    WHERE reqTimeSec >= baseline_start
      AND reqTimeSec < current_end
      AND {entity_expr} != ''
  )
SELECT
  {entity_expr} AS {entity_type},
  countMergeIf(`count()`, reqTimeSec >= current_start) AS current_requests,
  countMergeIf(`count()`, reqTimeSec < current_start) AS baseline_requests,
  if(current_requests > 0, countMergeIf(`count()`, reqTimeSec >= current_start AND cacheStatus = false) / current_requests * 100, 0) AS current_cache_miss_pct,
  if(baseline_requests > 0, countMergeIf(`count()`, reqTimeSec < current_start AND cacheStatus = false) / baseline_requests * 100, 0) AS baseline_cache_miss_pct,
  null AS current_unique_qs,
  null AS baseline_unique_qs,
  null AS current_origin_p95_ms,
  null AS baseline_origin_p95_ms,
  if(
    (SELECT cluster_requests FROM cluster_total) > 0,
    current_requests / (SELECT cluster_requests FROM cluster_total) * 100,
    null
  ) AS origin_cost_contribution_pct
FROM {table}
WHERE reqTimeSec >= baseline_start
  AND reqTimeSec < current_end
  AND {entity_expr} != ''
GROUP BY {entity_type}
ORDER BY current_requests DESC
{limit_clause}
""".strip()


def cache_origin_path_sql(
    database: str,
    start: datetime,
    end: datetime,
    baseline_start: datetime,
    host_filter: str | None,
    producer_limit: int,
) -> str:
    granularity = choose_granularity(start, end)
    table = f"{database}.bot_agg_path_{granularity}"
    host_clause = f"\n  AND request_host = '{host_filter}'" if host_filter else ""
    limit_clause = f"\nLIMIT {producer_limit}" if producer_limit > 0 else ""
    return f"""
WITH
  toDateTime('{sql_ts(start)}', 'UTC') AS current_start,
  toDateTime('{sql_ts(end)}', 'UTC') AS current_end,
  toDateTime('{sql_ts(baseline_start)}', 'UTC') AS baseline_start
SELECT
  request_host,
  request_path_norm,
  sumIf(cnt_all, timestamp >= current_start AND timestamp < current_end) AS current_requests,
  sumIf(cnt_all, timestamp >= baseline_start AND timestamp < current_start) AS baseline_requests,
  sumIf(cnt_cache_miss, timestamp >= current_start AND timestamp < current_end) AS current_cache_misses,
  sumIf(cnt_cache_miss, timestamp >= baseline_start AND timestamp < current_start) AS baseline_cache_misses,
  sumIf(uniq_qs, timestamp >= current_start AND timestamp < current_end) AS current_unique_query_strings,
  sumIf(uniq_qs, timestamp >= baseline_start AND timestamp < current_start) AS baseline_unique_query_strings,
  maxIf(p95_origin_ttfb, timestamp >= current_start AND timestamp < current_end) AS current_origin_p95_ms,
  maxIf(p95_origin_ttfb, timestamp >= baseline_start AND timestamp < current_start) AS baseline_origin_p95_ms
FROM {table}
WHERE timestamp >= baseline_start
  AND timestamp < current_end{host_clause}
GROUP BY request_host, request_path_norm
ORDER BY current_requests DESC
{limit_clause}
""".strip()


def metric_by_name(artifact: dict) -> dict[str, dict]:
    return {
        str(metric.get("name")): metric
        for metric in artifact.get("metrics", [])
        if isinstance(metric, dict) and metric.get("name")
    }


def rate_row(
    name: str, label: str, numerator: str, denominator: str, metrics: dict[str, dict]
) -> dict:
    num = metrics.get(numerator, {})
    den = metrics.get(denominator, {})
    current = pct(num.get("current"), den.get("current"))
    baseline = pct(num.get("baseline"), den.get("baseline"))
    delta_points = None if current is None or baseline is None else current - baseline
    return {
        "name": name,
        "label": label,
        "current_pct": current,
        "baseline_pct": baseline,
        "delta_points": delta_points,
        "current_display": human_number(current, percent=True)
        if current is not None
        else "unavailable",
        "baseline_display": human_number(baseline, percent=True)
        if baseline is not None
        else "unavailable",
        "delta_points_display": human_number(delta_points, percent=True, signed=True)
        if delta_points is not None
        else "unavailable",
        "change_label": label_change(delta_points),
    }


def metric_card_from_metric(metric: dict) -> dict:
    name = str(metric.get("name", ""))
    return {
        "name": name,
        "label": METRIC_LABELS.get(name, name),
        "current": metric.get("current"),
        "baseline": metric.get("baseline"),
        "absolute_delta": metric.get("absolute_delta"),
        "pct_change": metric.get("pct_change"),
        "current_display": human_number(metric.get("current")),
        "baseline_display": human_number(metric.get("baseline")),
        "absolute_delta_display": human_number(
            metric.get("absolute_delta"), signed=True
        ),
        "pct_change_display": human_number(
            metric.get("pct_change"), percent=True, signed=True
        ),
        "direction": metric.get("direction"),
        "confidence": metric.get("confidence"),
        "change_label": label_change(metric.get("pct_change")),
    }


def metric_map_from_control_effects(artifact: dict) -> dict[str, dict]:
    metrics: dict[str, dict] = {}
    for effect in artifact.get("target_effects", []):
        if not isinstance(effect, dict) or not effect.get("metric"):
            continue
        name = str(effect["metric"])
        metrics[name] = {
            "name": name,
            "current": effect.get("after"),
            "baseline": effect.get("expected"),
            "absolute_delta": effect.get("absolute_delta_vs_expected"),
            "pct_change": effect.get("pct_change_vs_expected"),
            "direction": effect.get("direction"),
            "confidence": effect.get("confidence"),
        }
    return metrics


def standard_derived_rates(metrics: dict[str, dict]) -> list[dict]:
    return [
        rate_row(
            "bot_like_share_pct",
            "Bot-like share",
            "bot_like_requests",
            "requests",
            metrics,
        ),
        rate_row("ai_share_pct", "AI share", "ai_requests", "requests", metrics),
        rate_row(
            "cache_miss_rate_pct",
            "Cache miss rate",
            "cache_misses",
            "requests",
            metrics,
        ),
        rate_row(
            "rate_limited_rate_pct",
            "429 rate-limit rate",
            "rate_limited_requests",
            "requests",
            metrics,
        ),
        rate_row(
            "error_5xx_rate_pct",
            "5xx error rate",
            "error_5xx_requests",
            "requests",
            metrics,
        ),
    ]


def control_followups(args: argparse.Namespace) -> list[dict]:
    if args.control_source == "posture":
        return [
            {
                "question": "Which ASNs drove the bot-like request movement?",
                "capture_preset": "posture-by-asn",
            },
            {
                "question": "Which paths drove the cache-miss or 429 movement?",
                "capture_preset": "posture-by-path",
            },
            {
                "question": "If SIEM summaries are available for another scope, do policy outcomes line up with this posture movement?",
                "capture_preset": "siem-policy",
            },
        ]
    return [
        {
            "question": "Which policy, action, or bot type drove the after-window movement?",
            "capture_preset": "siem-policy",
        },
        {
            "question": "Did protected crawler or verified bot populations see collateral rate-limit or deny changes?",
            "capture_preset": "siem-policy",
        },
        {
            "question": "Did traffic shift to other ASNs, paths, hosts, or bot categories after the control changed?",
            "capture_preset": "posture-by-asn",
        },
    ]


def build_evidence_packet(
    *,
    args: argparse.Namespace,
    artifact: dict,
    raw_path: Path,
    artifact_path: Path,
    granularity: str,
    table_used: str,
    baseline_start: datetime,
) -> dict:
    metrics = metric_by_name(artifact)
    metric_cards = []
    for metric in artifact.get("metrics", []):
        if not isinstance(metric, dict):
            continue
        metric_cards.append(metric_card_from_metric(metric))

    derived_rates = standard_derived_rates(metrics)

    total = metrics.get("requests", {})
    bot_like = metrics.get("bot_like_requests", {})
    ai = metrics.get("ai_requests", {})
    cache = metrics.get("cache_misses", {})
    findings = []
    for source, title in (
        (total, "Total request volume changed"),
        (bot_like, "Bot-like request volume changed"),
        (ai, "AI request volume changed"),
        (cache, "Cache-miss volume changed"),
    ):
        if not source:
            continue
        findings.append(
            {
                "title": title,
                "change_label": label_change(source.get("pct_change")),
                "evidence": (
                    f"{human_number(source.get('current'))} current vs "
                    f"{human_number(source.get('baseline'))} baseline "
                    f"({human_number(source.get('pct_change'), percent=True, signed=True)})."
                ),
            }
        )

    return {
        "schema_version": "bot_report_evidence.v1",
        "report_type": args.report,
        "title": args.title or "Bot & Edge Movement",
        "scope": {"cluster": args.cluster, "database": args.database},
        "query_context": {
            "cluster": args.cluster,
            "database": args.database,
            "table_used": table_used,
            "granularity": granularity,
            "raw_artifact_path": str(raw_path),
            "deterministic_artifact_path": str(artifact_path),
        },
        "current_window": artifact.get("current_window"),
        "baseline_windows": artifact.get("baseline_windows"),
        "metric_cards": metric_cards,
        "derived_rates": derived_rates,
        "headline_findings": findings,
        "suggested_followups": [
            {
                "question": "Which ASNs drove the bot-like request movement?",
                "capture_preset": "posture-by-asn",
            },
            {
                "question": "Which paths drove the cache-miss movement?",
                "capture_preset": "posture-by-path",
            },
            {
                "question": "Do SIEM policy outcomes line up with the bot-like movement?",
                "capture_preset": "siem-policy",
            },
        ],
        "interpretation_contract": {
            "allowed": [
                "Summarize only the fields in this packet.",
                "Compare metric changes and derived rates.",
                "Recommend follow-up queries from suggested_followups.",
            ],
            "forbidden": [
                "Do not claim root cause.",
                "Do not call traffic malicious without additional evidence.",
                "Do not introduce values not present in this packet.",
                "Do not query Hydrolix from the interpretation step.",
            ],
        },
        "template": {
            "sections": [
                "Executive Summary",
                "Key Changes",
                "Operational Interpretation",
                "Recommended Follow-ups",
                "Method and Caveats",
            ]
        },
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "baseline_start": baseline_start.isoformat().replace("+00:00", "Z"),
    }


def build_control_evidence_packet(
    *,
    args: argparse.Namespace,
    artifact: dict,
    raw_path: Path,
    artifact_path: Path,
    granularity: str,
    table_used: str,
    baseline_start: datetime,
) -> dict:
    metrics = metric_map_from_control_effects(artifact)
    metric_cards = [metric_card_from_metric(metric) for metric in metrics.values()]
    derived_rates = standard_derived_rates(metrics)
    effect_cards = []
    findings = []
    for effect in artifact.get("target_effects", []):
        if not isinstance(effect, dict):
            continue
        metric = str(effect.get("metric", ""))
        card = {
            "metric": metric,
            "label": METRIC_LABELS.get(metric, metric),
            "before": effect.get("before"),
            "after": effect.get("after"),
            "expected": effect.get("expected"),
            "absolute_delta_vs_expected": effect.get("absolute_delta_vs_expected"),
            "pct_change_vs_expected": effect.get("pct_change_vs_expected"),
            "before_display": human_number(effect.get("before")),
            "after_display": human_number(effect.get("after")),
            "expected_display": human_number(effect.get("expected")),
            "absolute_delta_vs_expected_display": human_number(
                effect.get("absolute_delta_vs_expected"),
                signed=True,
            ),
            "pct_change_vs_expected_display": human_number(
                effect.get("pct_change_vs_expected"),
                percent=True,
                signed=True,
            ),
            "direction": effect.get("direction"),
            "status": effect.get("status"),
            "confidence": effect.get("confidence"),
        }
        effect_cards.append(card)
        findings.append(
            {
                "title": f"{card['label']} vs expected",
                "change_label": str(effect.get("status") or "not evaluated"),
                "evidence": (
                    f"{card['after_display']} after vs {card['expected_display']} expected "
                    f"({card['pct_change_vs_expected_display']})."
                ),
            }
        )

    return {
        "schema_version": "bot_report_evidence.v1",
        "report_type": args.report,
        "title": args.title or "Bot Insights Control Review",
        "scope": {"cluster": args.cluster, "database": args.database},
        "query_context": {
            "cluster": args.cluster,
            "database": args.database,
            "table_used": table_used,
            "granularity": granularity,
            "raw_artifact_path": str(raw_path),
            "deterministic_artifact_path": str(artifact_path),
        },
        "change_time": artifact.get("change_time"),
        "target": artifact.get("target"),
        "before_window": artifact.get("before_window"),
        "after_window": artifact.get("after_window"),
        "expected_window": artifact.get("expected_window"),
        "expected_basis": artifact.get("expected_basis"),
        "target_effects": effect_cards,
        "metric_cards": metric_cards,
        "derived_rates": derived_rates,
        "collateral_checks": artifact.get("collateral_checks", []),
        "displacement_checks": artifact.get("displacement_checks", []),
        "headline_findings": findings,
        "suggested_followups": control_followups(args),
        "interpretation_contract": {
            "allowed": [
                "Summarize only the fields in this packet.",
                "Compare after-window metrics, derived rates, and expected values.",
                "Describe control-review caveats and recommend follow-up checks.",
            ],
            "forbidden": [
                "Do not claim the control caused the movement without external change evidence.",
                "Do not call traffic malicious without additional artifacts.",
                "Do not introduce values not present in this packet.",
                "Do not query Hydrolix from the interpretation step.",
                "Do not emit final HTML or Markdown layout.",
            ],
        },
        "template": {
            "sections": [
                "Control Review Summary",
                "Target Effects",
                "Collateral and Displacement Checks",
                "Operational Interpretation",
                "Recommended Follow-ups",
                "Method and Caveats",
            ]
        },
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "baseline_start": baseline_start.isoformat().replace("+00:00", "Z"),
    }


def selected_rank(index: dict, card: dict) -> int | None:
    for row in index.get("ranked_entities", []):
        if (
            isinstance(row, dict)
            and row.get("entity_type") == card.get("entity_type")
            and row.get("entity") == card.get("entity")
        ):
            rank = row.get("rank")
            return (
                int(rank)
                if isinstance(rank, int) and not isinstance(rank, bool)
                else None
            )
    return None


def select_scorecard(
    artifacts: dict,
    *,
    entity_type: str | None = None,
    entity_value: str | None = None,
) -> dict:
    scorecards = artifacts.get("scorecards")
    if not isinstance(scorecards, list) or not scorecards:
        raise SystemExit("Scorecard artifacts did not contain any emitted scorecards.")

    if entity_type or entity_value:
        if not entity_type or entity_value is None:
            raise SystemExit(
                "--entity-type and --entity-value must be supplied together."
            )
        for card in scorecards:
            if (
                isinstance(card, dict)
                and card.get("entity_type") == entity_type
                and str(card.get("entity")) == entity_value
            ):
                return card
        raise SystemExit(f"No scorecard found for {entity_type}={entity_value}.")

    index = artifacts.get("index")
    ranked = index.get("ranked_entities") if isinstance(index, dict) else None
    if isinstance(ranked, list) and ranked:
        top = ranked[0]
        if isinstance(top, dict):
            for card in scorecards:
                if (
                    isinstance(card, dict)
                    and card.get("entity_type") == top.get("entity_type")
                    and card.get("entity") == top.get("entity")
                ):
                    return card
    return scorecards[0]


SOC_INTERPRETATION_CONTRACT: dict[str, list[str]] = {
    "allowed": [
        "Summarize the SIEM-active SOC scorecard rows and emitted security_evidence features.",
        "Use score, band, confidence, blocked-request and auth-failure volumes, and recommended next steps.",
        "Describe SOC rowset limits and missing security inputs explicitly.",
    ],
    "forbidden": [
        "Do not call traffic malicious without additional artifacts.",
        "Do not invent SIEM metrics or other security evidence inputs.",
        "Do not query Hydrolix from the interpretation step.",
        "Do not emit final HTML or Markdown layout.",
    ],
}

SOC_TEMPLATE_SECTIONS = [
    "SOC Triage Summary",
    "Top Risky Entities",
    "Selected Entity",
    "Domain Scores",
    "Evaluated Security Evidence",
    "Missing Security Inputs",
    "Recommended Next Steps",
    "Method and Caveats",
]


CRAWLER_INTERPRETATION_CONTRACT: dict[str, list[str]] = {
    "allowed": [
        "Summarize the emitted crawler_governance scorecard features and rowset population.",
        "Use score, band, confidence, missing inputs, and recommended next steps.",
        "Describe rowset-limit caveats and missing crawler inputs explicitly.",
    ],
    "forbidden": [
        "Do not claim malicious crawler intent without additional artifacts.",
        "Do not invent missing feature inputs.",
        "Do not query Hydrolix from the interpretation step.",
        "Do not emit final HTML or Markdown layout.",
    ],
}

CRAWLER_TEMPLATE_SECTIONS = [
    "Crawler Governance Summary",
    "Top Crawler Entities",
    "Selected Entity",
    "Domain Scores",
    "Evaluated Crawler Evidence",
    "Missing Crawler Inputs",
    "Recommended Next Steps",
    "Method and Caveats",
]


EDGE_OPS_INTERPRETATION_CONTRACT: dict[str, list[str]] = {
    "allowed": [
        "Summarize the emitted edge_ops_impact scorecard features and entity population.",
        "Use score, band, confidence, missing inputs, and recommended next steps.",
        "Describe origin cost contribution and cache miss movement using only the emitted evidence.",
        "Describe rowset-limit caveats and missing edge/ops inputs explicitly.",
    ],
    "forbidden": [
        "Do not claim origin billing cost without real byte-level evidence.",
        "Do not invent missing feature inputs.",
        "Do not query Hydrolix from the interpretation step.",
        "Do not emit final HTML or Markdown layout.",
    ],
}

EDGE_OPS_TEMPLATE_SECTIONS = [
    "Edge & Origin Cost Summary",
    "Top Entities by Origin Pressure",
    "Selected Entity",
    "Domain Scores",
    "Evaluated Edge/Ops Evidence",
    "Top Cache-Impacting Paths",
    "Missing Edge/Ops Inputs",
    "Recommended Next Steps",
    "Method and Caveats",
]


def build_scorecard_evidence_packet(
    *,
    args: argparse.Namespace,
    artifacts: dict,
    selected_card: dict,
    raw_path: Path,
    artifact_path: Path,
    granularity: str,
    table_used: str,
    baseline_start: datetime,
) -> dict:
    index = artifacts.get("index") if isinstance(artifacts.get("index"), dict) else {}
    if args.report == "soc_triage":
        default_title = "Bot Insights SOC Triage"
        interpretation_contract = SOC_INTERPRETATION_CONTRACT
        template_sections = SOC_TEMPLATE_SECTIONS
    elif args.report == "crawler_governance":
        default_title = "Bot Insights Crawler Governance"
        interpretation_contract = CRAWLER_INTERPRETATION_CONTRACT
        template_sections = CRAWLER_TEMPLATE_SECTIONS
    elif args.report == "edge_ops_impact":
        default_title = "Bot Insights Edge & Origin Cost"
        interpretation_contract = EDGE_OPS_INTERPRETATION_CONTRACT
        template_sections = EDGE_OPS_TEMPLATE_SECTIONS
    else:
        default_title = "Bot Insights Scorecard Brief"
        interpretation_contract = {
            "allowed": [
                "Summarize only the selected scorecard entity and emitted feature evidence.",
                "Use score, band, confidence, domain scores, missing inputs, and recommended next steps.",
                "Describe rowset limits and provenance caveats when present.",
            ],
            "forbidden": [
                "Do not invent metrics or missing scorecard inputs.",
                "Do not query Hydrolix from the interpretation step.",
                "Do not claim root cause or malicious intent from scorecard rules alone.",
                "Do not emit final HTML or Markdown layout.",
            ],
        }
        template_sections = [
            "Scorecard Interpretation",
            "Selected Entity",
            "Domain Scores",
            "Evaluated Feature Evidence",
            "Missing Scorecard Inputs",
            "Recommended Next Steps",
            "Method and Caveats",
        ]
    return {
        "schema_version": "bot_report_evidence.v1",
        "report_type": args.report,
        "title": args.title or default_title,
        "scope": {"cluster": args.cluster, "database": args.database},
        "query_context": {
            "cluster": args.cluster,
            "database": args.database,
            "table_used": table_used,
            "granularity": granularity,
            "raw_artifact_path": str(raw_path),
            "deterministic_artifact_path": str(artifact_path),
            "producer_limit": args.scorecard_limit,
            "entity_selection": "explicit" if args.entity_value else "top_ranked",
        },
        "selected_entity": {
            "entity_type": selected_card.get("entity_type"),
            "entity": selected_card.get("entity"),
            "rank": selected_rank(index, selected_card),
            "score": selected_card.get("score"),
            "band": selected_card.get("band"),
            "primary_domain": selected_card.get("primary_domain"),
            "confidence": selected_card.get("confidence"),
            "confidence_reasons": selected_card.get("confidence_reasons", []),
        },
        "domain_scores": selected_card.get("domain_scores", {}),
        "rule_results": selected_card.get("rule_results", []),
        "evaluated_feature_evidence": selected_card.get("features", []),
        "not_evaluated_features": selected_card.get("not_evaluated_features", []),
        "missing_inputs": sorted(
            {
                str(missing_input)
                for feature in selected_card.get("not_evaluated_features", [])
                if isinstance(feature, dict)
                for missing_input in feature.get("missing_inputs", [])
            }
        ),
        "recommended_next_steps": selected_card.get("recommended_next_steps", []),
        "evidence_summary": selected_card.get("evidence_summary", []),
        "rowset_context": {
            "rowset_scope": selected_card.get("rowset_scope"),
            "feature_provenance": selected_card.get("feature_provenance"),
            "producer_limit": artifacts.get("producer_limit")
            or index.get("producer_limit"),
            "result_row_count": artifacts.get("result_row_count")
            or index.get("result_row_count"),
            "result_truncated": artifacts.get("result_truncated")
            or index.get("result_truncated"),
            "total_ranked_entities": artifacts.get("total_ranked_entities")
            or index.get("total_ranked_entities"),
        },
        "current_window": selected_card.get("current_window"),
        "baseline_windows": selected_card.get("baseline_windows"),
        "analysis_domains": selected_card.get("analysis_domains")
        or index.get("analysis_domains"),
        "interpretation_contract": interpretation_contract,
        "template": {"sections": template_sections},
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "baseline_start": baseline_start.isoformat().replace("+00:00", "Z"),
    }


def load_raw_query_result(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"data": value, "rows": len(value)}
    raise SystemExit(
        f"Expected {path} to contain a Hydrolix MCP or ClickHouse JSON object."
    )


def result_rows(value: dict) -> list[dict]:
    rows = value.get("data")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    rows = value.get("rows")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []


def build_timeseries_artifact(
    *,
    args: argparse.Namespace,
    raw_value: dict,
    control_artifact: dict,
    table_used: str,
    granularity: str,
) -> dict:
    metrics = metric_map_from_control_effects(control_artifact)
    before_by_metric: dict[str, list[dict]] = {name: [] for name in metrics}
    after_by_metric: dict[str, list[dict]] = {name: [] for name in metrics}
    for row in result_rows(raw_value):
        period = str(row.get("period", "")).lower()
        bucket = row.get("bucket")
        if period not in {"before", "after"} or bucket is None:
            continue
        for name in metrics:
            value = row.get(name)
            point = {"timestamp": bucket, "value": value}
            if period == "before":
                before_by_metric[name].append(point)
            else:
                after_by_metric[name].append(point)

    series = []
    for name, metric in metrics.items():
        before_values = sorted(
            before_by_metric[name], key=lambda item: str(item.get("timestamp"))
        )
        after_values = sorted(
            after_by_metric[name], key=lambda item: str(item.get("timestamp"))
        )
        length = max(len(before_values), len(after_values))
        points = []
        for index in range(length):
            before_point = before_values[index] if index < len(before_values) else {}
            after_point = after_values[index] if index < len(after_values) else {}
            points.append(
                {
                    "baseline_timestamp": before_point.get("timestamp"),
                    "current_timestamp": after_point.get("timestamp"),
                    "baseline": before_point.get("value"),
                    "current": after_point.get("value"),
                }
            )
        if points:
            series.append(
                {
                    "name": name,
                    "label": METRIC_LABELS.get(name, name),
                    "current": metric.get("current"),
                    "baseline": metric.get("baseline"),
                    "absolute_delta": metric.get("absolute_delta"),
                    "pct_change": metric.get("pct_change"),
                    "points": points,
                }
            )

    return {
        "schema_version": "bot_timeseries.v1",
        "artifact_id": f"{args.report}-timeseries",
        "title": "Control Review Trends",
        "report_type": "control_review",
        "scope": control_artifact.get("scope", {}),
        "table_used": table_used,
        "granularity": granularity,
        "current_window": control_artifact.get("after_window", {}),
        "baseline_windows": [control_artifact.get("before_window", {})],
        "metrics": series,
        "interpretation_constraints": [
            "trend_shape_only",
            "no_causal_claim",
            "llm_may_summarize_structured_evidence_only",
        ],
    }


def add_report_metadata(
    *,
    raw_value: dict,
    args: argparse.Namespace,
    granularity: str,
    table_used: str,
    baseline_start: datetime,
) -> dict:
    enriched = dict(raw_value)
    enriched.update(
        {
            "comparison_type": "previous_window",
            "granularity": granularity,
            "table_used": table_used,
            "scope": {
                "cluster": args.cluster,
                "database": args.database,
            },
            "current_window": {
                "start": args.start,
                "end": args.end,
            },
            "baseline_windows": [
                {
                    "start": baseline_start.isoformat().replace("+00:00", "Z"),
                    "end": args.start,
                }
            ],
        }
    )
    return enriched


def add_control_metadata(
    *,
    raw_value: dict,
    args: argparse.Namespace,
    granularity: str,
    table_used: str,
    baseline_start: datetime,
) -> dict:
    enriched = dict(raw_value)
    if args.control_source == "posture":
        target = {"control_scope": "posture_summary"}
        target_metrics = [
            "requests",
            "bot_like_requests",
            "ai_requests",
            "cache_misses",
            "rate_limited_requests",
            "error_5xx_requests",
        ]
    else:
        target = (
            {"policy_id": args.policy_id}
            if args.policy_id
            else {"policy_scope": "all_policies"}
        )
        target_metrics = [
            "siem_blocked_requests",
            "siem_auth_fail_requests",
            "requests",
            "avg_bot_score",
            "unique_client_ips",
        ]
    enriched.update(
        {
            "comparison_type": "post_change_vs_expected",
            "granularity": granularity,
            "table_used": table_used,
            "change_time": args.change_time or args.start,
            "target": target,
            "scope": {
                "cluster": args.cluster,
                "database": args.database,
            },
            "before_window": {
                "start": baseline_start.isoformat().replace("+00:00", "Z"),
                "end": args.start,
            },
            "after_window": {
                "start": args.start,
                "end": args.end,
            },
            "expected_window": {
                "start": baseline_start.isoformat().replace("+00:00", "Z"),
                "end": args.start,
            },
            "expected_basis": "before_window",
            "target_metrics": target_metrics,
        }
    )
    return enriched


def add_scorecard_metadata(
    *,
    raw_value: dict,
    args: argparse.Namespace,
    granularity: str,
    table_used: str,
    baseline_start: datetime,
) -> dict:
    enriched = dict(raw_value)
    enriched.update(
        {
            "comparison_type": "previous_window",
            "granularity": granularity,
            "table_used": table_used,
            "scope": {
                "cluster": args.cluster,
                "database": args.database,
                "entity_type": args.entity_type,
            },
            "current_window": {
                "start": args.start,
                "end": args.end,
            },
            "baseline_windows": [
                {
                    "start": baseline_start.isoformat().replace("+00:00", "Z"),
                    "end": args.start,
                }
            ],
            "summary_table_used": True,
            "rowset_complete": False,
            "source_row_count": enriched.get("rows"),
            "producer_limit": args.scorecard_limit,
        }
    )
    if args.domains:
        enriched["analysis_domains"] = [
            item.strip() for item in args.domains.split(",") if item.strip()
        ]
    if args.report == "crawler_governance":
        population = CRAWLER_POPULATION_BY_ENTITY.get(args.entity_type)
        if population is not None:
            enriched["rowset_scope"] = {"population": population}
    return enriched


def analyst_note_from_args(args: argparse.Namespace) -> dict | None:
    text = args.analyst_notes
    if args.analyst_notes_file:
        text = Path(args.analyst_notes_file).expanduser().read_text(encoding="utf-8")
    if not text:
        return None
    return {
        "note_id": "llm-interpretation",
        "author_type": "llm",
        "title": {
            "executive_posture": "Executive Interpretation",
            "control_review": "Control Review Interpretation",
            "scorecard_brief": "Scorecard Interpretation",
            "soc_triage": "SOC Triage Interpretation",
            "crawler_governance": "Crawler Governance Interpretation",
            "edge_ops_impact": "Edge & Origin Cost Interpretation",
        }.get(args.report, "Analyst Interpretation"),
        "text": text.strip(),
        "show_data_sources": False,
        "data_sources": [],
    }


def build_report_wrapper(
    *,
    args: argparse.Namespace,
    artifacts: list[dict],
    analyst_note: dict | None = None,
) -> dict:
    wrapper = {
        "schema_version": "bot_report_input.v1",
        "report_type": args.report,
        "title": args.title
        or {
            "executive_posture": "Bot & Edge Movement",
            "control_review": "Bot Insights Control Review",
            "scorecard_brief": "Bot Insights Scorecard Brief",
            # The auto-generated form lowercases the SOC acronym ("Soc
            # Triage") which reads wrong; spell it explicitly.
            "soc_triage": "SOC Triage",
            "crawler_governance": "Crawler Governance",
            "edge_ops_impact": "Edge & Origin Cost",
        }.get(args.report, f"Bot Insights {args.report.replace('_', ' ').title()}"),
        "scope_label": f"{args.cluster}/{args.database}",
        "artifacts": artifacts,
        "analyst_notes": [analyst_note] if analyst_note else [],
    }
    return wrapper


def render_template_packet(packet: dict) -> str:
    findings = "\n".join(
        f"- {item['title']}: {item['evidence']}"
        for item in packet.get("headline_findings", [])
    )
    rates = "\n".join(
        "- "
        + f"{rate['label']}: {rate['current_display']} current vs "
        + f"{rate['baseline_display']} baseline "
        + f"({rate['delta_points_display']} percentage points)."
        for rate in packet.get("derived_rates", [])
    )
    metrics = "\n".join(
        "- "
        + f"{metric['label']}: {metric['current_display']} current vs "
        + f"{metric['baseline_display']} baseline; "
        + f"{metric['pct_change_display']} change."
        for metric in packet.get("metric_cards", [])
    )
    effects = "\n".join(
        "- "
        + f"{effect['label']}: {effect['after_display']} after vs "
        + f"{effect['expected_display']} expected; "
        + f"{effect['pct_change_vs_expected_display']} vs expected."
        for effect in packet.get("target_effects", [])
    )
    selected_entity = packet.get("selected_entity") or {}
    domain_scores = "\n".join(
        f"- {domain}: {score}"
        for domain, score in (packet.get("domain_scores") or {}).items()
    )
    feature_evidence = "\n".join(
        f"- {feature.get('domain')}/{feature.get('name')}: {feature.get('evidence')}"
        for feature in packet.get("evaluated_feature_evidence", [])
        if isinstance(feature, dict)
    )
    followups = (
        "\n".join(
            f"- {item['question']} (`{item['capture_preset']}`)"
            for item in packet["suggested_followups"]
        )
        if "suggested_followups" in packet
        else "\n".join(
            f"- {item['detail'] if isinstance(item, dict) else item}"
            for item in packet.get("recommended_next_steps", [])
        )
    )
    context = packet["query_context"]
    return f"""# {packet["title"]}

## Executive Summary

LLM: Write 2-4 concise sentences using only the evidence below. Do not infer root cause.

## Key Changes

{findings or "- No headline findings available."}

## Rates

{rates or "- No derived rates available."}

## Metrics

{metrics or "- No metrics available."}

## Control Effects

{effects or "- No control effects available."}

## Selected Scorecard Entity

- Entity: {selected_entity.get("entity_type", "unavailable")}={selected_entity.get("entity", "unavailable")}
- Rank: {selected_entity.get("rank", "unavailable")}
- Score: {selected_entity.get("score", "unavailable")}
- Band: {selected_entity.get("band", "unavailable")}
- Confidence: {selected_entity.get("confidence", "unavailable")}

## Domain Scores

{domain_scores or "- No domain scores available."}

## Evaluated Feature Evidence

{feature_evidence or "- No evaluated feature evidence available."}

## Operational Interpretation

LLM: Explain what the changes may mean operationally. Keep this as hypotheses or checks, not causal claims.

## Recommended Follow-ups

{followups}

## Method and Caveats

- Data source: `{context["table_used"]}`
- Cluster: `{context["cluster"]}`
- Database: `{context["database"]}`
- Granularity: `{context["granularity"]}`
- Current/after window: `{json.dumps(packet.get("current_window") or packet.get("after_window"), sort_keys=True)}`
- Baseline/before windows: `{json.dumps(packet.get("baseline_windows") or packet.get("before_window"), sort_keys=True)}`
- This report is based on deterministic summary-table evidence. It does not identify root cause by itself.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bot-insights-report",
        description="Generate Bot Insights reports from Hydrolix summary data via local artifacts.",
    )
    parser.add_argument(
        "--cluster", required=True, help="Hydrolix cluster alias or .env file path."
    )
    parser.add_argument(
        "--database", default="akamai", help="Hydrolix database/project."
    )
    parser.add_argument(
        "--report",
        choices=(
            "executive_posture",
            "control_review",
            "scorecard_brief",
            "soc_triage",
            "crawler_governance",
            "edge_ops_impact",
        ),
        default="executive_posture",
        help="Report type to generate.",
    )
    parser.add_argument(
        "--mode",
        choices=("report", "evidence", "template"),
        default="report",
        help="Output a deterministic report, an LLM evidence packet, or a Markdown template scaffold.",
    )
    parser.add_argument(
        "--start", required=True, help="Inclusive ISO-8601 current-window start."
    )
    parser.add_argument(
        "--end", required=True, help="Exclusive ISO-8601 current-window end."
    )
    parser.add_argument(
        "--baseline-start",
        help="Inclusive ISO-8601 baseline start. Defaults to the equal-length previous window.",
    )
    parser.add_argument(
        "--sample-dir",
        help="Directory for intermediate local JSON. Defaults to ~/src/sample-data/bot-insights/1.1/<cluster>.",
    )
    parser.add_argument(
        "--output", required=True, help="Output path for the selected mode."
    )
    parser.add_argument(
        "--raw-input",
        help="Resume from a saved Hydrolix MCP or ClickHouse JSON result instead of running capture.",
    )
    parser.add_argument(
        "--raw-path-input",
        type=str,
        default=None,
        help="Resume edge_ops_impact from a saved path-grain JSON result alongside --raw-input.",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "html"),
        default="html",
        help="Rendered report format.",
    )
    parser.add_argument("--title", help="Optional rendered report title.")
    parser.add_argument(
        "--policy-id", help="Optional SIEM policyId filter for control_review."
    )
    parser.add_argument(
        "--control-source",
        choices=("siem-policy", "posture"),
        default="siem-policy",
        help="Summary surface for control_review evidence.",
    )
    parser.add_argument(
        "--change-time",
        help="Optional control change timestamp. Defaults to --start for control_review.",
    )
    parser.add_argument(
        "--entity-type",
        choices=tuple(SCORECARD_ENTITY_SQL),
        default="request_host",
        help="Entity type to score for scorecard_brief.",
    )
    parser.add_argument(
        "--entity-value",
        help="Optional explicit entity value to render for scorecard_brief. Defaults to top-ranked scorecard entity.",
    )
    parser.add_argument(
        "--scorecard-limit",
        type=int,
        default=20,
        help="Maximum aggregate rows/scorecards to keep for scorecard_brief.",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Optional hostname filter for edge_ops_impact path-grain query (scopes path candidates to a single request_host).",
    )
    parser.add_argument(
        "--include-paths",
        action="store_true",
        default=False,
        help=(
            "Opt in to the edge_ops_impact path-grain capture against "
            "bot_agg_path_<granularity>. This table is not currently "
            "deployed on any production cluster, so the path-grain query "
            "is off by default; enabling it falls back gracefully when "
            "the table is missing."
        ),
    )
    parser.add_argument(
        "--domains",
        help="Optional comma-separated scorecard domains to evaluate.",
    )
    parser.add_argument(
        "--analyst-notes",
        help="LLM interpretation prose to include in the final report wrapper.",
    )
    parser.add_argument(
        "--analyst-notes-file",
        help="Read LLM interpretation prose from a file for the final report wrapper.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = parse_time(args.start, "start")
    end = parse_time(args.end, "end")
    window = end - start
    if args.baseline_start:
        baseline_start = parse_time(args.baseline_start, "baseline-start")
    else:
        baseline_start = start - window
    if baseline_start >= start:
        raise SystemExit("--baseline-start must be earlier than --start")
    if args.scorecard_limit < 0:
        raise SystemExit("--scorecard-limit must be zero or a positive integer.")
    scorecard_reports = {
        "scorecard_brief",
        "soc_triage",
        "crawler_governance",
        "edge_ops_impact",
    }
    if args.report not in scorecard_reports and args.entity_value:
        raise SystemExit(
            "--entity-value is only supported with --report scorecard_brief, --report soc_triage, "
            "--report crawler_governance, or --report edge_ops_impact."
        )
    if args.report == "soc_triage" and args.entity_type not in SOC_ENTITY_SQL:
        raise SystemExit(
            "--entity-type "
            + args.entity_type
            + " is not supported for soc_triage; use one of "
            + ", ".join(sorted(SOC_ENTITY_SQL))
        )
    if (
        args.report == "crawler_governance"
        and args.entity_type not in CRAWLER_ENTITY_SQL
    ):
        raise SystemExit(
            "--entity-type "
            + args.entity_type
            + " is not supported for crawler_governance; use one of "
            + ", ".join(sorted(CRAWLER_ENTITY_SQL))
        )
    if args.report == "edge_ops_impact" and args.entity_type not in EDGE_OPS_ENTITY_SQL:
        raise SystemExit(
            "--entity-type "
            + args.entity_type
            + " is not supported for edge_ops_impact; use one of "
            + ", ".join(sorted(EDGE_OPS_ENTITY_SQL))
        )
    if args.raw_path_input and not args.raw_input:
        raise SystemExit(
            "--raw-path-input requires --raw-input to also be supplied "
            "(both raw inputs must be provided to resume an edge_ops_impact run)."
        )
    if args.raw_path_input and args.report != "edge_ops_impact":
        raise SystemExit(
            "--raw-path-input is only valid with --report edge_ops_impact."
        )
    if args.report == "soc_triage" and not args.domains:
        # SOC scorecards must evaluate only the security_evidence domain so
        # crawler/Edge/Ops features do not surface as missing SOC evidence.
        args.domains = "security_evidence"
    if args.report == "crawler_governance" and not args.domains:
        # Crawler governance scorecards must evaluate only the
        # crawler_governance domain so SOC/Edge features do not surface as
        # missing crawler evidence.
        args.domains = "crawler_governance"
    if args.report == "edge_ops_impact" and not args.domains:
        # Edge/Ops scorecards evaluate cache_busting and origin_impact domains
        # so SOC/crawler features do not surface as missing edge evidence.
        args.domains = "cache_busting,origin_impact"

    sample_dir = (
        Path(args.sample_dir).expanduser().resolve()
        if args.sample_dir
        else DEFAULT_SAMPLE_ROOT / args.cluster
    )
    sample_dir.mkdir(parents=True, exist_ok=True)
    raw_path = sample_dir / f"{args.report}-raw.json"
    artifact_path = sample_dir / f"{args.report}-artifact.json"
    timeseries_raw_path = sample_dir / f"{args.report}-timeseries-raw.json"
    timeseries_artifact_path = sample_dir / f"{args.report}-timeseries.json"
    path_raw_path = sample_dir / f"{args.report}-path-raw.json"
    path_artifact_path = sample_dir / f"{args.report}-path-artifact.json"
    wrapper_path = sample_dir / f"{args.report}-wrapper.json"
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.report == "executive_posture":
        sql = executive_posture_sql(args.database, start, end, baseline_start)
        granularity = choose_granularity(start, end)
        table_used = f"{args.database}.bi_summary_{granularity}"
        compare_schema = "posture"
    elif args.report == "control_review":
        sql = control_review_sql(
            args.database,
            start,
            end,
            baseline_start,
            args.policy_id,
            args.control_source,
        )
        granularity = choose_granularity(start, end)
        if args.control_source == "posture":
            table_used = f"{args.database}.bi_summary_{granularity}"
        else:
            table_used = f"{args.database}.bi_siem_policy_summary_{granularity}"
        compare_schema = "control"
    elif args.report == "scorecard_brief":
        sql = scorecard_sql(
            args.database,
            start,
            end,
            baseline_start,
            args.entity_type,
            args.scorecard_limit,
        )
        granularity = choose_granularity(start, end)
        table_used = f"{args.database}.bi_summary_{granularity}"
        compare_schema = None
    elif args.report == "soc_triage":
        sql = scorecard_soc_sql(
            args.database,
            start,
            end,
            baseline_start,
            args.entity_type,
            args.scorecard_limit,
        )
        granularity = choose_granularity(start, end)
        table_used = f"{args.database}.bi_siem_policy_summary_{granularity}"
        compare_schema = None
    elif args.report == "crawler_governance":
        sql = scorecard_crawler_sql(
            args.database,
            start,
            end,
            baseline_start,
            args.entity_type,
            args.scorecard_limit,
        )
        granularity = choose_granularity(start, end)
        table_used = f"{args.database}.bi_summary_{granularity}"
        compare_schema = None
    elif args.report == "edge_ops_impact":
        sql = scorecard_edge_ops_sql(
            args.database,
            start,
            end,
            baseline_start,
            args.entity_type,
            args.scorecard_limit,
        )
        granularity = choose_granularity(start, end)
        table_used = f"{args.database}.bi_summary_{granularity}"
        compare_schema = None
    else:
        raise AssertionError(args.report)

    capture_summary: dict[str, object] = {"rows": None}
    raw_timeseries_value: dict | None = None
    raw_path_value: dict | None = None
    if args.raw_input:
        raw_value = load_raw_query_result(Path(args.raw_input).expanduser().resolve())
        if args.report == "control_review" and timeseries_raw_path.exists():
            raw_timeseries_value = load_raw_query_result(timeseries_raw_path)
        if args.report == "edge_ops_impact":
            if args.raw_path_input:
                raw_path_value = load_raw_query_result(
                    Path(args.raw_path_input).expanduser().resolve()
                )
            elif args.include_paths:
                print(
                    "WARNING: --raw-path-input not supplied for edge_ops_impact; "
                    "path-grain artifact will be omitted.",
                    file=sys.stderr,
                )
    else:
        try:
            capture_summary_text = run(
                [
                    sys.executable,
                    str(CAPTURE),
                    "--cluster",
                    args.cluster,
                    "--database",
                    args.database,
                    "--sql",
                    sql,
                    "--output",
                    str(raw_path),
                ],
                allowed_returncodes=(NEEDS_MCP_EXIT,),
            )
        except SystemExit as exc:
            # SOC triage depends on bi_siem_policy_summary_<granularity>,
            # which is not deployed on every cluster. Without SIEM data the
            # script cannot produce a SOC report, so warn clearly and exit
            # cleanly rather than crash with a raw capture traceback.
            if args.report == "soc_triage":
                print(
                    "WARNING: SOC capture failed; "
                    f"{table_used} may not be deployed on this cluster ({exc}). "
                    "soc_triage requires SIEM policy summary data; skipping report.",
                    file=sys.stderr,
                )
                return 0
            raise
        try:
            capture_summary = json.loads(capture_summary_text)
        except json.JSONDecodeError as exc:
            raise SystemExit("Capture did not return machine-readable JSON.") from exc
        if (
            isinstance(capture_summary, dict)
            and capture_summary.get("schema_version") == HANDOFF_SCHEMA
        ):
            report_context = capture_summary.get("report_context")
            if not isinstance(report_context, dict):
                report_context = {}
            report_context.update(
                {
                    "report": args.report,
                    "mode": args.mode,
                    "start": args.start,
                    "end": args.end,
                    "baseline_start": baseline_start.isoformat().replace("+00:00", "Z"),
                    "table_used": table_used,
                    "granularity": granularity,
                }
            )
            if args.report in {
                "scorecard_brief",
                "soc_triage",
                "crawler_governance",
                "edge_ops_impact",
            }:
                report_context.update(
                    {
                        "entity_type": args.entity_type,
                        "entity_value": args.entity_value,
                        "producer_limit": args.scorecard_limit,
                        "analysis_domains": args.domains,
                    }
                )
            if args.report == "edge_ops_impact":
                report_context["artifact"] = "scorecard"
            capture_summary["report_context"] = report_context
            print(json.dumps(capture_summary, sort_keys=True))
            return NEEDS_MCP_EXIT
        raw_value = load_raw_query_result(raw_path)
        if args.report == "control_review":
            timeseries_sql = control_review_timeseries_sql(
                args.database,
                start,
                end,
                baseline_start,
                args.policy_id,
                args.control_source,
            )
            timeseries_summary_text = run(
                [
                    sys.executable,
                    str(CAPTURE),
                    "--cluster",
                    args.cluster,
                    "--database",
                    args.database,
                    "--sql",
                    timeseries_sql,
                    "--output",
                    str(timeseries_raw_path),
                ],
                allowed_returncodes=(NEEDS_MCP_EXIT,),
            )
            try:
                timeseries_summary = json.loads(timeseries_summary_text)
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    "Timeseries capture did not return machine-readable JSON."
                ) from exc
            if (
                isinstance(timeseries_summary, dict)
                and timeseries_summary.get("schema_version") == HANDOFF_SCHEMA
            ):
                report_context = timeseries_summary.get("report_context")
                if not isinstance(report_context, dict):
                    report_context = {}
                report_context.update(
                    {
                        "report": args.report,
                        "mode": args.mode,
                        "artifact": "timeseries",
                        "start": args.start,
                        "end": args.end,
                        "baseline_start": baseline_start.isoformat().replace(
                            "+00:00", "Z"
                        ),
                        "table_used": table_used,
                        "granularity": granularity,
                    }
                )
                timeseries_summary["report_context"] = report_context
                print(json.dumps(timeseries_summary, sort_keys=True))
                return NEEDS_MCP_EXIT
            raw_timeseries_value = load_raw_query_result(timeseries_raw_path)
        if args.report == "edge_ops_impact" and args.include_paths:
            path_grain_sql = cache_origin_path_sql(
                args.database,
                start,
                end,
                baseline_start,
                args.host,
                args.scorecard_limit,
            )
            path_table_used = f"{args.database}.bot_agg_path_{granularity}"
            try:
                path_capture_text = run(
                    [
                        sys.executable,
                        str(CAPTURE),
                        "--cluster",
                        args.cluster,
                        "--database",
                        args.database,
                        "--sql",
                        path_grain_sql,
                        "--output",
                        str(path_raw_path),
                    ],
                    allowed_returncodes=(NEEDS_MCP_EXIT,),
                )
            except SystemExit as exc:
                # Path-grain summary table may not exist on every cluster
                # (bot_agg_path_* is optional infrastructure). Degrade
                # gracefully to entity-grain only.
                print(
                    f"WARNING: path-grain capture failed ({exc}); "
                    "path artifact will be omitted.",
                    file=sys.stderr,
                )
                path_capture_text = ""
            try:
                path_capture_summary = json.loads(path_capture_text) if path_capture_text else {}
            except json.JSONDecodeError:
                print(
                    "WARNING: path-grain capture did not return machine-readable JSON; "
                    "path artifact will be omitted.",
                    file=sys.stderr,
                )
                path_capture_summary = {}
            if (
                isinstance(path_capture_summary, dict)
                and path_capture_summary.get("schema_version") == HANDOFF_SCHEMA
            ):
                path_report_context = path_capture_summary.get("report_context")
                if not isinstance(path_report_context, dict):
                    path_report_context = {}
                path_report_context.update(
                    {
                        "report": args.report,
                        "mode": args.mode,
                        "artifact": "path",
                        "start": args.start,
                        "end": args.end,
                        "baseline_start": baseline_start.isoformat().replace(
                            "+00:00", "Z"
                        ),
                        "table_used": path_table_used,
                        "granularity": granularity,
                    }
                )
                path_capture_summary["report_context"] = path_report_context
                print(json.dumps(path_capture_summary, sort_keys=True))
                return NEEDS_MCP_EXIT
            if path_raw_path.exists():
                raw_path_value = load_raw_query_result(path_raw_path)

    if args.report == "executive_posture":
        raw_value = add_report_metadata(
            raw_value=raw_value,
            args=args,
            granularity=granularity,
            table_used=table_used,
            baseline_start=baseline_start,
        )
    elif args.report == "control_review":
        raw_value = add_control_metadata(
            raw_value=raw_value,
            args=args,
            granularity=granularity,
            table_used=table_used,
            baseline_start=baseline_start,
        )
    elif args.report in {
        "scorecard_brief",
        "soc_triage",
        "crawler_governance",
        "edge_ops_impact",
    }:
        raw_value = add_scorecard_metadata(
            raw_value=raw_value,
            args=args,
            granularity=granularity,
            table_used=table_used,
            baseline_start=baseline_start,
        )
    else:
        raise AssertionError(args.report)
    raw_path.write_text(
        json.dumps(raw_value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    if args.report in {
        "scorecard_brief",
        "soc_triage",
        "crawler_governance",
        "edge_ops_impact",
    }:
        scorecard_cmd = [
            "uv",
            "run",
            "python",
            "skills/bot-insights/scripts/scorecard.py",
            "--file",
            str(raw_path),
            "--entity-type",
            args.entity_type,
            "--limit",
            str(args.scorecard_limit),
        ]
        if args.domains:
            scorecard_cmd.extend(["--domains", args.domains])
        run(scorecard_cmd, stdout_path=artifact_path, cwd=PUBLIC_SKILLS)
    else:
        run(
            [
                "uv",
                "run",
                "python",
                "skills/bot-insights/scripts/compare_posture.py",
                "--file",
                str(raw_path),
                "--schema",
                compare_schema,
            ],
            stdout_path=artifact_path,
            cwd=PUBLIC_SKILLS,
        )

    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if not isinstance(artifact, dict):
        raise SystemExit(f"Expected {artifact_path} to contain an artifact object.")
    companion_artifacts: list[dict] = []
    path_artifact: dict | None = None
    if args.report == "edge_ops_impact" and raw_path_value is not None:
        path_raw_path.write_text(
            json.dumps(raw_path_value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            path_cmd = [
                "uv",
                "run",
                "python",
                "skills/bot-insights/scripts/cache_origin_impact.py",
                "--file",
                str(path_raw_path),
            ]
            run(path_cmd, stdout_path=path_artifact_path, cwd=PUBLIC_SKILLS)
            path_artifact = json.loads(path_artifact_path.read_text(encoding="utf-8"))
            if not isinstance(path_artifact, dict) or not path_artifact.get(
                "candidates"
            ):
                print(
                    "WARNING: path-grain artifact has no candidates; "
                    "path artifact will be omitted.",
                    file=sys.stderr,
                )
                path_artifact = None
        except Exception as exc:  # noqa: BLE001
            print(
                f"WARNING: path-grain processing failed ({exc}); "
                "path artifact will be omitted.",
                file=sys.stderr,
            )
            path_artifact = None
    if args.report == "control_review" and raw_timeseries_value is not None:
        timeseries_artifact = build_timeseries_artifact(
            args=args,
            raw_value=raw_timeseries_value,
            control_artifact=artifact,
            table_used=table_used,
            granularity=granularity,
        )
        if timeseries_artifact.get("metrics"):
            timeseries_artifact_path.write_text(
                json.dumps(timeseries_artifact, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            companion_artifacts.append(timeseries_artifact)
    if args.report == "executive_posture":
        evidence_packet = build_evidence_packet(
            args=args,
            artifact=artifact,
            raw_path=raw_path,
            artifact_path=artifact_path,
            granularity=granularity,
            table_used=table_used,
            baseline_start=baseline_start,
        )
    elif args.report == "control_review":
        evidence_packet = build_control_evidence_packet(
            args=args,
            artifact=artifact,
            raw_path=raw_path,
            artifact_path=artifact_path,
            granularity=granularity,
            table_used=table_used,
            baseline_start=baseline_start,
        )
    elif args.report in {
        "scorecard_brief",
        "soc_triage",
        "crawler_governance",
        "edge_ops_impact",
    }:
        selected_card = select_scorecard(
            artifact,
            entity_type=args.entity_type if args.entity_value else None,
            entity_value=args.entity_value,
        )
        evidence_packet = build_scorecard_evidence_packet(
            args=args,
            artifacts=artifact,
            selected_card=selected_card,
            raw_path=raw_path,
            artifact_path=artifact_path,
            granularity=granularity,
            table_used=table_used,
            baseline_start=baseline_start,
        )
    else:
        raise AssertionError(args.report)

    if args.mode == "evidence":
        output_path.write_text(
            json.dumps(evidence_packet, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    elif args.mode == "template":
        output_path.write_text(
            render_template_packet(evidence_packet), encoding="utf-8"
        )
    else:
        if args.report == "scorecard_brief":
            selected_card = select_scorecard(
                artifact,
                entity_type=args.entity_type if args.entity_value else None,
                entity_value=args.entity_value,
            )
            render_artifacts = [selected_card]
            if isinstance(artifact.get("index"), dict):
                render_artifacts.append(artifact["index"])
        elif args.report in {"soc_triage", "crawler_governance"}:
            render_artifacts = []
            if isinstance(artifact.get("index"), dict):
                render_artifacts.append(artifact["index"])
            scorecards = artifact.get("scorecards")
            if isinstance(scorecards, list):
                render_artifacts.extend(
                    card for card in scorecards if isinstance(card, dict)
                )
        elif args.report == "edge_ops_impact":
            render_artifacts = []
            if isinstance(artifact.get("index"), dict):
                render_artifacts.append(artifact["index"])
            scorecards = artifact.get("scorecards")
            if isinstance(scorecards, list):
                render_artifacts.extend(
                    card for card in scorecards if isinstance(card, dict)
                )
            if path_artifact is not None:
                render_artifacts.append(path_artifact)
        else:
            render_artifacts = [artifact, *companion_artifacts]
        wrapper = build_report_wrapper(
            args=args,
            artifacts=render_artifacts,
            analyst_note=analyst_note_from_args(args),
        )
        wrapper_path.write_text(
            json.dumps(wrapper, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        render_cmd = [
            "uv",
            "run",
            "python",
            "skills/bot-insights/scripts/render_report.py",
            "--file",
            str(wrapper_path),
            "--format",
            args.format,
            "--output",
            str(output_path),
        ]
        if args.title:
            render_cmd.extend(["--title", args.title])
        run(render_cmd, cwd=PUBLIC_SKILLS)

    print(
        json.dumps(
            {
                "artifact": str(artifact_path),
                "cluster": args.cluster,
                "database": args.database,
                "granularity": granularity,
                "mode": args.mode,
                "raw": str(raw_path),
                "output": str(output_path),
                "rows": capture_summary.get("rows"),
                "table_used": table_used,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
