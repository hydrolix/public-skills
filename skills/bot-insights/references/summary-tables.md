# bot-insights — Summary Tables

Bot Insights has request-level records plus summary tables at minute, hour, and
day granularity. Prefer summaries when their retained dimensions answer the
question. Fall back to request-level `bot_detection` or `bot_detection_siem`
only when the required field is missing from the summary surface.

Before querying a deployed Hydrolix summary table, inspect table metadata with
the Hydrolix MCP server or the host agent's Hydrolix query tool. If a metric is
stored as an aggregate state, query it with the merge function reported by the
tool. Do not add database clients or credentials to local scripts.

Deployments may expose both current bundle table names and backwards-compatible
`bi_*` table names:

- `bi_summary_*` mirrors the canonical posture dimensions expected from
  `bot_summary_*`. It is used by both TrafficPeak and non-TrafficPeak
  deployments and should be treated as the preferred posture summary surface
  for the `akamai` project. Prefer fully qualified `akamai.bi_summary_*` over
  `akamai.bot_summary_*` when both are present.
- `bi_siem_summary_*` mirrors the host/action/ASN/policy SIEM summary expected
  from `bot_siem_summary_*`. It is used by both TrafficPeak and
  non-TrafficPeak deployments and should be treated as the preferred SIEM
  control summary surface for the `akamai` project. Prefer fully qualified
  `akamai.bi_siem_summary_*`; if a deployment exposes
  `akamai.bi_summary_siem_*`, treat that as an equivalent deployment-specific
  alias after metadata inspection.
- Some alternate or legacy tables may expose source-style Akamai names, such as
  `reqTimeSec`, `reqHost`, `asn`, `statusCode`, `totalBytes`, and
  `cacheStatus`.

Use metadata as the source of truth for query text. Keep analysis artifacts and
local script inputs canonical when possible. The attribution SQL renderer
accepts canonical dimension and filter names, resolves them to metadata-backed
physical columns, and aliases grouped output back to canonical names.

When the project/database name is absent or equals `akamai`, start discovery as
though the target is probably TrafficPeak and inspect `bi_summary_*` /
`bi_siem_summary_*` before `bot_summary_*` / `bot_siem_summary_*`. When the
project/database name is present and is not `akamai`, start discovery as though
the target is probably non-TrafficPeak. This is a routing heuristic for table
discovery only; always confirm with table metadata before writing SQL.

## Attribution SQL Template Rendering

The local attribution script exposes a reviewed template renderer for future
direct-MCP wrappers. Example wrapper fragment:

```python
render_attribution_sql_template(
    table_metadata=hydrolix_table_metadata,
    metric="requests",
    dimensions=["client_asn"],
    scope={"request_host": "www.example.com"},
    current_window={"start": "2026-04-01T00:00:00Z", "end": "2026-04-02T00:00:00Z"},
    baseline_windows=[
        {"start": "2026-03-01T00:00:00Z", "end": "2026-03-02T00:00:00Z"}
    ],
    baseline_method="single_previous_window",
    output_limit=50,
)
```

The renderer returns SQL plus provenance and assertion evidence payloads. It
does not execute SQL, read credentials, compute `result_digest`, emit scorecard
artifacts, or make any output high-confidence by itself. A future reviewed
direct-MCP wrapper must run the SQL, receive the tool result in memory, compute
the result digest, and carry matching evidence into the normalizer.

When metadata exposes source-style Akamai fields, canonical renderer input such
as `dimensions=["client_asn"]` and `scope={"request_host": "www.example.com"}`
may render SQL that groups by `asn AS client_asn` and filters on `reqHost`.
The renderer records these translations in provenance under `column_aliases`.

For aggregate-state columns, the renderer uses the exact `merge_function`
reported in table metadata. For example a metadata column named
`sum(cnt_all)` with `merge_function: sumMerge` renders
``sumMerge(`sum(cnt_all)`)``; it does not infer merge functions from column
names. Current and baseline metrics are computed in separate period-scoped CTEs
with explicit current and baseline predicates, so non-adjacent baseline windows
do not scan the gap between baseline and current periods. Contribution
denominators are computed in the `scored` CTE before the final output `LIMIT`.

## Selection Rules

- Use day summaries for quarter-over-quarter, month-over-month, year-over-year,
  same-week-last-year, and executive posture movement.
- Use hour summaries for same-weekday-hour-last-week, same-hour-yesterday,
  daily rhythm, and weekday/hour seasonality.
- Use minute summaries for short policy-change review, detailed timelines, and
  incident-style follow-up.
- Choose the narrowest summary that retains the requested dimensions. If a
  requested dimension is absent, either answer at the retained dimension level
  or explicitly fall back to raw request-level data with a tight time filter.
- Do not assume quarter-over-quarter queries need monthly or quarterly
  summaries. Benchmark against daily summaries first.

## Inventory

| Table | Granularity | Parent | Retained dimensions | Metric support |
|-------|-------------|--------|---------------------|----------------|
| `bi_summary_day` / `bot_summary_day` | day | `bot_detection` | `timestamp`, `request_host`, `hdx_cdn`, `bot_class`, `ai_category`, `is_bot_traffic`, `client_asn`, `asn_type`, `resource_category`, `request_method` | requests, 2xx/4xx/429/5xx, cache hit/miss, avg TTFB, avg/p95/p99 origin TTFB, unique client IPs, source latency |
| `bi_summary_hour` / `bot_summary_hour` | hour | `bot_detection` | same as `bi_summary_day` | same as `bi_summary_day` |
| `bi_summary_minute` / `bot_summary_minute` | minute | `bot_detection` | same as `bi_summary_day` | same as `bi_summary_day` |
| `bi_summary_month` | month | `bot_detection` | same as `bi_summary_day` when deployed | same as `bi_summary_day` when deployed |
| `bot_agg_hour` | hour | `bot_detection` | `timestamp`, `request_host` | requests, 2xx/4xx/429/5xx, cache hit/miss, avg TTFB, avg/p95/p99 origin TTFB, unique client IPs, source latency |
| `bot_agg_asn_hour` | hour | `bot_detection` | `timestamp`, `request_host`, `client_asn`, `asn_type` | same as `bot_agg_hour`, plus unique normalized paths |
| `bot_agg_traffic_hour` | hour | `bot_detection` | `timestamp`, `request_host`, `is_bot_traffic`, `ai_category` | same as `bot_agg_hour` |
| `bot_agg_ua_hour` | hour | `bot_detection` | `timestamp`, `request_host`, `bot_class` | same as `bot_agg_hour` |
| `bot_agg_path_day` | day | `bot_detection` | `timestamp`, `request_host`, `request_path_norm`, `bot_class`, `asn_type` | requests, 2xx/4xx/429/5xx, cache hit/miss, avg TTFB, avg/p95/p99 origin TTFB, unique client IPs, unique query strings, source latency |
| `bot_agg_path_hour` | hour | `bot_detection` | same as `bot_agg_path_day` | same as `bot_agg_path_day` |
| `bot_agg_path_minute` | minute | `bot_detection` | same as `bot_agg_path_day` | same as `bot_agg_path_day` |
| `bot_agg_resource_day` | day | `bot_detection` | `timestamp`, `request_host`, `resource_category` | same as `bot_agg_path_day` |
| `bot_agg_resource_hour` | hour | `bot_detection` | same as `bot_agg_resource_day` | same as `bot_agg_path_day` |
| `bot_agg_resource_minute` | minute | `bot_detection` | same as `bot_agg_resource_day` | same as `bot_agg_path_day` |
| `bi_siem_summary_day` / `bot_siem_summary_day` | day | `bot_detection_siem` | `timestamp`, `request_host`, `action_taken`, `client_asn`, `policy_id` | requests, blocked requests, auth failures, business failures, avg bot score, 2xx/4xx/5xx, unique client IPs, cache misses |
| `bi_siem_summary_hour` / `bot_siem_summary_hour` | hour | `bot_detection_siem` | same as `bi_siem_summary_day` | same as `bi_siem_summary_day` |
| `bi_siem_summary_minute` / `bot_siem_summary_minute` | minute | `bot_detection_siem` | same as `bi_siem_summary_day` | same as `bi_siem_summary_day` |
| `bot_siem_filter_summary_day` | day | `bot_detection_siem` | `timestamp`, `request_host`, `client_asn`, `is_bot_traffic`, `ai_category`, `resource_category` | same metric family as `bi_siem_summary_day` / `bot_siem_summary_day` |
| `bot_siem_filter_summary_hour` | hour | `bot_detection_siem` | same as `bot_siem_filter_summary_day` | same metric family as `bi_siem_summary_day` / `bot_siem_summary_day` |
| `bot_siem_filter_summary_minute` | minute | `bot_detection_siem` | same as `bot_siem_filter_summary_day` | same metric family as `bi_siem_summary_day` / `bot_siem_summary_day` |
| `bot_siem_class_day` | day | `bot_detection_siem` | `timestamp`, `request_host`, `client_asn`, `akamai_canonical_bot_class` | requests, avg bot score, unique client IPs |
| `bot_siem_class_hour` | hour | `bot_detection_siem` | same as `bot_siem_class_day` | same as `bot_siem_class_day` |
| `bot_siem_class_minute` | minute | `bot_detection_siem` | same as `bot_siem_class_day` | same as `bot_siem_class_day` |

## Metric Aliases

Common summary metric columns:

- `cnt_all`: request count.
- `cnt_2xx`, `cnt_4xx`, `cnt_429`, `cnt_5xx`: status-code families.
- `cnt_cached`, `cnt_cache_miss`: cache outcome counts.
- `avg_ttfb`, `avg_origin_ttfb`: average edge/origin latency.
- `p95_origin_ttfb`, `p99_origin_ttfb`: tail origin latency.
- `uniq_client_ip`: unique client IP count.
- `uniq_paths`: unique normalized path count on `bot_agg_asn_hour`.
- `uniq_qs`: unique query-string count on path and resource summaries.
- `cnt_blocked`, `cnt_auth_fail`, `cnt_biz_fail`: SIEM control outcomes.
- `avg_bot_score`: average Akamai bot score on SIEM summaries.

Derived posture metrics should be computed from aggregate rows, for example:

- `bot_share_pct = sumIf(cnt_all, is_bot_traffic = true) / sum(cnt_all) * 100`
- `bad_bot_share_pct = sumIf(cnt_all, bot_class = 'bad') / sum(cnt_all) * 100`
- `ai_crawler_share_pct = sumIf(cnt_all, ai_category != '') / sum(cnt_all) * 100`
- `cache_miss_pct = sum(cnt_cache_miss) / sum(cnt_all) * 100`
- `rate_429_pct = sum(cnt_429) / sum(cnt_all) * 100`
- `rate_5xx_pct = sum(cnt_5xx) / sum(cnt_all) * 100`

If metadata reports aggregate-state columns, replace `sum(...)` and
`sumIf(...)` over SummaryColumns with the aggregate column's exact merge
function. For example, use the reported `countMerge` function around the
`count()` aggregate column for total requests, and `countMergeIf` around that
same aggregate column for bot-request subsets when supported.

## Raw Fallback Dimensions

Use request-level tables when the question depends on fields not retained in the
summary catalog, such as `verified_bot_owner`, `bot_confidence`, `bot_intent`,
`bot_category`, `bot_type`, `client_country_iso_code`, `edge_pop`,
`response_status_code`, `attack_data`, `user_agent`, or `user_agent_category`.
State the fallback reason and keep the time range narrow.
