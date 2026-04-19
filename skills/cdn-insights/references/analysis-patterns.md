# cdn-insights — Analysis Patterns

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
    countIf(response_status_code >= '500') as errors_5xx,
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
  AND response_status_code >= '400'
GROUP BY response_status_code, request_path
ORDER BY errors DESC
LIMIT 20

-- Errors by CDN provider
SELECT hdx_cdn, response_status_code, count() as cnt
FROM <project>.cdn_insights
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND response_status_code >= '400'
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
    countIf(response_status_code >= '500') as errors,
    avg(response_time_to_first_byte_ms) as avg_ttfb_ms
FROM <project>.cdn_insights
WHERE timestamp >= now() - INTERVAL 1 HOUR

UNION ALL

SELECT
    'yesterday' as period,
    count() as requests,
    countIf(response_status_code >= '500') as errors,
    avg(response_time_to_first_byte_ms) as avg_ttfb_ms
FROM <project>.cdn_insights
WHERE timestamp >= now() - INTERVAL 25 HOUR
  AND timestamp < now() - INTERVAL 24 HOUR
```
