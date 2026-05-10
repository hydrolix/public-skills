# bot-insights — Summary Tables

Bot Insights query surface is the summary tables `bi_summary_*` and (on
SIEM-enabled clusters) `bi_siem_policy_summary_*`. Older skill iterations
documented request-level (`bot_detection`, `bot_detection_siem`) and focused
aggregate families (`bot_agg_path_*`, `bot_agg_resource_*`, `bot_agg_ua_*`);
those rows remain in the inventory below for design-intent reference and are
flagged "NOT CURRENTLY DEPLOYED". Do not generate SQL against them. When a
question truly needs a request-level dimension, state the limitation rather
than falling back to a non-deployed table.

Before querying a deployed Hydrolix summary table, inspect table metadata with
the Hydrolix MCP server or the host agent's Hydrolix query tool. If a metric is
stored as an aggregate state, query it with the merge function reported by the
tool. Do not add database clients or credentials to local scripts.

Spec table names for the TrafficPeak Akamai project:

- `bi_summary_*` is the posture summary surface. Use fully qualified
  `akamai.bi_summary_minute`, `akamai.bi_summary_hour`, or
  `akamai.bi_summary_day`. These tables retain source-style fields including
  `reqTimeSec`, `reqHost`, `asn`, `userAgentCategory`, `isBotTraffic`,
  `aiCategory`, `aiSource`, `trafficCohort`, `resourceCategory`, `reqMethod`,
  `cacheStatus`, `statusCode`, `requestPathPattern`, and `country`.
- `bi_siem_policy_summary_*` is the with-SIEM dashboard surface. Use fully
  qualified `akamai.bi_siem_policy_summary_minute`,
  `akamai.bi_siem_policy_summary_hour`, or
  `akamai.bi_siem_policy_summary_day`. These tables retain `timestamp`,
  `host`/`reqHost`, `asn`, `userAgentCategory`, `isBotTraffic`, `aiCategory`,
  `aiSource`, `resourceCategory`, `method`/`reqMethod`,
  `status`/`statusCode`, `country`, `policyId`, `actionClass`, and `botType`.

Use metadata as the source of truth for query text. Keep analysis artifacts and
local script inputs canonical when possible. The attribution SQL renderer
accepts canonical dimension and filter names, resolves them to metadata-backed
physical columns, and aliases grouped output back to canonical names.

For `demo.trafficpeak.live`, start from the `akamai` database and confirm table
metadata before writing SQL.

## Contents

- [Attribution SQL Template Rendering](#attribution-sql-template-rendering)
- [Selection Rules](#selection-rules)
- [Inventory](#inventory)
- [Metric Aliases](#metric-aliases)
- [Request-Level Dimensions](#request-level-dimensions)

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

- Use minute summaries for windows under 3 hours.
- Use hour summaries for windows under 48 hours.
- Use day summaries for windows of 48 hours or longer, including
  quarter-over-quarter, month-over-month, year-over-year,
  same-week-last-year, and executive posture movement.
- Choose the narrowest summary that retains the requested dimensions. If a
  requested dimension is absent, either answer at the retained dimension level
  or explicitly fall back to raw request-level data with a tight time filter.
- Do not use request-level raw tables for standard Bot Insights report
  captures. Standard reports must use `bi_summary_*` or
  `bi_siem_policy_summary_*`.
- Do not assume quarter-over-quarter queries need monthly or quarterly
  summaries. Benchmark against daily summaries first.

## Inventory

| Table | Granularity | Parent | Retained dimensions | Metric support |
|-------|-------------|--------|---------------------|----------------|
| `bi_summary_day` | day | `akamai.logs` | `reqTimeSec`, `reqHost`, `asn`, `userAgentCategory`, `isBotTraffic`, `aiCategory`, `aiSource`, `trafficCohort`, `resourceCategory`, `reqMethod`, `cacheStatus`, `statusCode`, `requestPathPattern`, `country` | requests, bytes, status mix, cache hit/miss, average origin TaT, average TTFB, query-string presence/diversity |
| `bi_summary_hour` | hour | same as `bi_summary_day` | same as `bi_summary_day` | same as `bi_summary_day` |
| `bi_summary_minute` | minute | same as `bi_summary_day` | same as `bi_summary_day` | same as `bi_summary_day` |
| `bi_summary_month` (NOT CURRENTLY DEPLOYED) | month | `bot_detection` | same as `bi_summary_day` when deployed | same as `bi_summary_day` when deployed |
| `bot_agg_hour` (NOT CURRENTLY DEPLOYED) | hour | `bot_detection` | `timestamp`, `request_host` | requests, 2xx/4xx/429/5xx, cache hit/miss, avg TTFB, avg/p95/p99 origin TTFB, unique client IPs, source latency |
| `bot_agg_asn_hour` (NOT CURRENTLY DEPLOYED) | hour | `bot_detection` | `timestamp`, `request_host`, `client_asn`, `asn_type` | same as `bot_agg_hour`, plus unique normalized paths |
| `bot_agg_traffic_hour` (NOT CURRENTLY DEPLOYED) | hour | `bot_detection` | `timestamp`, `request_host`, `is_bot_traffic`, `ai_category` | same as `bot_agg_hour` |
| `bot_agg_ua_hour` (NOT CURRENTLY DEPLOYED) | hour | `bot_detection` | `timestamp`, `request_host`, `bot_class` | same as `bot_agg_hour` |
| `bot_agg_path_day` (NOT CURRENTLY DEPLOYED) | day | `bot_detection` | `timestamp`, `request_host`, `request_path_norm`, `bot_class`, `asn_type` | requests, 2xx/4xx/429/5xx, cache hit/miss, avg TTFB, avg/p95/p99 origin TTFB, unique client IPs, unique query strings, source latency |
| `bot_agg_path_hour` (NOT CURRENTLY DEPLOYED) | hour | `bot_detection` | same as `bot_agg_path_day` | same as `bot_agg_path_day` |
| `bot_agg_path_minute` (NOT CURRENTLY DEPLOYED) | minute | `bot_detection` | same as `bot_agg_path_day` | same as `bot_agg_path_day` |
| `bot_agg_resource_day` (NOT CURRENTLY DEPLOYED) | day | `bot_detection` | `timestamp`, `request_host`, `resource_category` | same as `bot_agg_path_day` |
| `bot_agg_resource_hour` (NOT CURRENTLY DEPLOYED) | hour | `bot_detection` | same as `bot_agg_resource_day` | same as `bot_agg_path_day` |
| `bot_agg_resource_minute` (NOT CURRENTLY DEPLOYED) | minute | `bot_detection` | same as `bot_agg_resource_day` | same as `bot_agg_path_day` |
| `bi_siem_policy_summary_day` | day | `akamai.siem` | `timestamp`, `host`/`reqHost`, `asn`, `userAgentCategory`, `isBotTraffic`, `aiCategory`, `aiSource`, `resourceCategory`, `method`/`reqMethod`, `status`/`statusCode`, `country`, `policyId`, `actionClass`, `botType` | requests, blocked requests, auth failures, avg bot score, 2xx/3xx/4xx/5xx, unique client IPs |
| `bi_siem_policy_summary_hour` | hour | same as `bi_siem_policy_summary_day` | same as `bi_siem_policy_summary_day` | same as `bi_siem_policy_summary_day` |
| `bi_siem_policy_summary_minute` | minute | same as `bi_siem_policy_summary_day` | same as `bi_siem_policy_summary_day` | same as `bi_siem_policy_summary_day` |

## Metric Aliases

Common summary metric columns:

- `cnt_all`: request count.
- `cnt_2xx`, `cnt_4xx`, `cnt_429`, `cnt_5xx`: status-code families.
- `cnt_cached`, `cnt_cache_miss`: cache outcome counts.
- `avg_ttfb`, `avg_origin_ttfb`: average edge/origin latency.
- `p95_origin_ttfb`, `p99_origin_ttfb`: tail origin latency.
- `uniq_client_ip`: unique client IP count.
- `uniq_paths`: unique normalized path count on `bot_agg_asn_hour` (not
  currently deployed).
- `uniq_qs`: unique query-string count on path and resource summaries (not
  currently deployed).
- `cnt_blocked`, `cnt_auth_fail`, `cnt_biz_fail`: SIEM control outcomes.
- `avg_bot_score`: average Akamai bot score on SIEM summaries.
- TrafficPeak/Akamai SIEM aliases are camelCase: `cnt_authFail`,
  `avg_botScore`, and `uniq_clientIp`. Use those names or the exact
  aggregate-state merge functions from metadata.

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

## Request-Level Dimensions

Request-level (`bot_detection`, `bot_detection_siem`) and focused aggregate
(`bot_agg_*`) tables are not currently deployed on production clusters. When a
question depends on a field not retained in `bi_summary_*` or
`bi_siem_policy_summary_*` — for example `verified_bot_owner`,
`bot_confidence`, `bot_intent`, canonical `bot_category` or `bot_type`,
`edge_pop`, `attack_data`, or exact `user_agent` — state the limitation in the
artifact rather than substituting a non-deployed table. Summary-retained fields
include `trafficCohort`, `userAgentCategory`, `aiCategory`, `aiSource`,
`requestPathPattern`, numeric `statusCode`, `cacheStatus`, `policyId`,
`actionClass`, and `botType`.
