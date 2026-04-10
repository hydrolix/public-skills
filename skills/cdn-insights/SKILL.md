---
name: cdn-insights
description: >
  Analyze multi-CDN traffic data from a Hydrolix CDN Insights bundle deployment.
  Use when investigating request patterns, cache efficiency, origin health, edge
  performance, or error rates across Akamai, CloudFront, Fastly, Cloudflare, and
  other CDN sources.
license: Apache-2.0
metadata:
  version: 1.0.0
  author: Hydrolix
  bundle: cdn-insights
---

# CDN Insights Analysis

## What This Bundle Contains

The CDN Insights bundle ingests access logs from multiple CDN providers into a
unified `cdn_insights` table. Each CDN source has its own transform that normalizes
fields into a common schema while preserving provider-specific columns.

**Supported CDN sources**: Akamai (DS2), CloudFront (Firehose), Fastly, Cloudflare,
Tencent, BytePlus, CacheFly, Google Media CDN, Imperva, IOriver, Varnish

**Tables**:
- `cdn_insights` — primary table (all request-level records)
- `mcdn_summary_min` — minute-granularity pre-aggregated rollup
- `mcdn_summary_hour` — hourly pre-aggregated rollup

See `references/schema.md` for the full column inventory and
`references/summary-tables.md` for summary table structure.

## Discovery

Start by identifying what's available in the deployment:

```sql
-- List available tables in the project
-- Use list_tables to find the project and table names
-- The primary table is typically named cdn_insights

-- Check the time range of available data
SELECT min(timestamp), max(timestamp), count()
FROM <project>.<table>

-- See which CDN sources are present
SELECT hdx_cdn, count() as requests
FROM <project>.cdn_insights
GROUP BY hdx_cdn
ORDER BY requests DESC
```

## Key Normalized Columns

These columns are present across all CDN sources and are the primary dimensions
for analysis:

| Column | Description |
|--------|-------------|
| `timestamp` | Request timestamp |
| `request_host` | Hostname requested |
| `request_path` | URL path |
| `request_method` | HTTP method |
| `response_status_code` | HTTP status code |
| `response_total_bytes` | Response size in bytes |
| `cache_was_cached` | Whether the response was served from cache |
| `client_country_iso_code` | Client country |
| `client_city` | Client city |
| `client_asn` | Client autonomous system number |
| `edge_pop` | Edge point of presence |
| `user_agent_category` | Classified user agent type |
| `hdx_cdn` | Which CDN provider served this request |
| `response_time_to_first_byte_ms` | Time to first byte (ms) |
| `response_time_to_last_byte_ms` | Time to last byte (ms) |
| `origin_time_to_first_byte_ms` | Origin TTFB (ms) |
| `origin_time_to_last_byte_ms` | Origin TTLB (ms) |

## Analysis Patterns

### Traffic Overview

```sql
-- Request volume by CDN over time (use summary tables for large ranges)
SELECT
    toStartOfHour(timestamp) as hour,
    hdx_cdn,
    sumMerge(cnt_all) as requests
FROM <project>.mcdn_summary_hour
WHERE timestamp >= now() - INTERVAL 24 HOUR
GROUP BY hour, hdx_cdn
ORDER BY hour

-- Top hostnames by volume
SELECT request_host, count() as requests, sum(response_total_bytes) as bytes
FROM <project>.cdn_insights
WHERE timestamp >= now() - INTERVAL 1 HOUR
GROUP BY request_host
ORDER BY requests DESC
LIMIT 20
```

### Cache Efficiency

```sql
-- Cache hit ratio by hostname
SELECT
    request_host,
    count() as total,
    countIf(cache_was_cached = true) as hits,
    round(hits / total * 100, 2) as hit_rate_pct
FROM <project>.cdn_insights
WHERE timestamp >= now() - INTERVAL 1 HOUR
GROUP BY request_host
ORDER BY total DESC

-- Cache miss analysis — what paths miss most?
SELECT request_path, count() as misses
FROM <project>.cdn_insights
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND cache_was_cached = false
GROUP BY request_path
ORDER BY misses DESC
LIMIT 20
```

### Origin Health

```sql
-- Origin latency percentiles
SELECT
    quantile(0.5)(origin_time_to_first_byte_ms) as p50_ms,
    quantile(0.9)(origin_time_to_first_byte_ms) as p90_ms,
    quantile(0.99)(origin_time_to_first_byte_ms) as p99_ms
FROM <project>.cdn_insights
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND origin_time_to_first_byte_ms > 0

-- Origin error rates over time
SELECT
    toStartOfMinute(timestamp) as minute,
    count() as total,
    countIf(response_status_code >= 500) as errors_5xx,
    round(errors_5xx / total * 100, 2) as error_rate_pct
FROM <project>.cdn_insights
WHERE timestamp >= now() - INTERVAL 1 HOUR
GROUP BY minute
ORDER BY minute
```

### Error Investigation

```sql
-- Error breakdown by status code and path
SELECT
    response_status_code,
    request_path,
    count() as errors
FROM <project>.cdn_insights
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND response_status_code >= 400
GROUP BY response_status_code, request_path
ORDER BY errors DESC
LIMIT 20

-- Errors by CDN provider
SELECT hdx_cdn, response_status_code, count() as cnt
FROM <project>.cdn_insights
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND response_status_code >= 400
GROUP BY hdx_cdn, response_status_code
ORDER BY cnt DESC
```

### Geographic Analysis

```sql
-- Traffic by country
SELECT client_country_iso_code, count() as requests,
       sum(response_total_bytes) as bytes
FROM <project>.cdn_insights
WHERE timestamp >= now() - INTERVAL 1 HOUR
GROUP BY client_country_iso_code
ORDER BY requests DESC
LIMIT 20

-- Latency by edge POP
SELECT edge_pop,
    quantile(0.5)(response_time_to_first_byte_ms) as p50_ms,
    quantile(0.9)(response_time_to_first_byte_ms) as p90_ms,
    count() as requests
FROM <project>.cdn_insights
WHERE timestamp >= now() - INTERVAL 1 HOUR
GROUP BY edge_pop
ORDER BY requests DESC
LIMIT 20
```

### Comparative Analysis

When investigating anomalies, compare against a baseline period:

```sql
-- Compare current hour to same hour yesterday
SELECT
    'current' as period,
    count() as requests,
    countIf(response_status_code >= 500) as errors,
    avg(response_time_to_first_byte_ms) as avg_ttfb_ms
FROM <project>.cdn_insights
WHERE timestamp >= now() - INTERVAL 1 HOUR

UNION ALL

SELECT
    'yesterday' as period,
    count() as requests,
    countIf(response_status_code >= 500) as errors,
    avg(response_time_to_first_byte_ms) as avg_ttfb_ms
FROM <project>.cdn_insights
WHERE timestamp >= now() - INTERVAL 25 HOUR
  AND timestamp < now() - INTERVAL 24 HOUR
```

## Working With Summary Tables

Use summary tables (`mcdn_summary_min`, `mcdn_summary_hour`) for queries spanning
more than a few hours. They are pre-aggregated and much faster.

Summary tables use ClickHouse aggregate combiners. When re-aggregating pre-computed
values, use the `-Merge` suffix:

| Primary table | Summary table equivalent |
|---------------|------------------------|
| `count()` | `sumMerge(cnt_all)` |
| `sum(response_total_bytes)` | `sumMerge(response_total_bytes)` |
| `avg(response_time_to_first_byte_ms)` | `avgMerge(response_ttfb_ms)` |
| `quantile(0.5)(response_time_to_first_byte_ms)` | `quantilesMerge(0.5)(quantiles_response_ttfb_ms)` |

**Dimensions available in summary tables**: `timestamp`, `cache_was_cached`,
`response_status_code`, `request_host`, `client_country_iso_code`, `client_city`,
`client_asn`, `edge_pop`, `user_agent_category`, `hdx_cdn`

If you need a dimension not in the summary table, fall back to the primary table
with a narrower time window.

## Pitfalls

- **CDN-specific columns**: Many columns only exist for one CDN source (prefixed
  with `akamai_`, `cloudflare_`, etc.). Filter by `hdx_cdn` when using these.
- **Suppressed columns**: Columns marked `suppressed` in the schema are stored but
  excluded from default queries. They are typically raw/unnormalized variants of
  normalized fields. Use the normalized versions instead.
- **Large time ranges**: Always use summary tables for queries spanning more than a
  few hours. The primary table can have billions of rows.
- **response_total_bytes**: This is the normalized bytes field across all CDNs. Use
  this instead of CDN-specific byte fields.
