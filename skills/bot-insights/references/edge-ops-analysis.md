# bot-insights — Edge and Operations Analysis Patterns

Edge/Ops analysis should use summaries first for cache, origin, bandwidth
proxy, path, resource, bot class, and ASN movement. Use request-level fallback
only for exact query-string values, exact status codes, headers, or fields not
retained in summaries.

For structured cache-busting, query-string churn, cache-miss movement, or
origin-impact detector output, use
[cache-origin-impact.md](cache-origin-impact.md) first. That reference defines
the path-grain-only `cache_origin_impact_report.v1` boundary, confidence rules,
and metadata-aware SQL template requirements. This page remains a broader
Edge/Ops pattern reference.

### Cache-Busting and Querystring Churn Detection [Edge/Ops]

Bots that append unique query strings to every request defeat cache key matching,
causing artificial cache misses and origin overload.

```sql
-- Summary-backed querystring diversity by normalized path.
SELECT
    request_path_norm,
    bot_class,
    sum(cnt_all) as requests,
    sum(uniq_qs) as unique_qs,
    round(unique_qs / greatest(requests, 1), 4) as qs_diversity_ratio,
    sum(cnt_cache_miss) as cache_misses,
    round(cache_misses / greatest(requests, 1) * 100, 2) as miss_rate_pct
FROM <project>.bot_agg_path_hour
WHERE timestamp >= now() - INTERVAL 24 HOUR
GROUP BY request_path_norm, bot_class
HAVING requests > 100
ORDER BY qs_diversity_ratio DESC
LIMIT 20

-- Querystring churn by ASN requires raw fallback for exact query strings.
SELECT
    client_asn,
    request_path,
    count() as requests,
    uniq(request_query_string) as unique_qs,
    round(unique_qs / requests, 4) as qs_diversity_ratio
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND is_bot_traffic = true
  AND request_query_string != ''
GROUP BY client_asn, request_path
HAVING requests > 50
ORDER BY qs_diversity_ratio DESC
LIMIT 20
```

### Origin Impact and Bandwidth Cost [Edge/Ops]

```sql
-- Origin latency and cache impact by bot class from summaries.
SELECT
    bot_class,
    sum(cnt_all) as requests,
    avg(avg_origin_ttfb) as avg_origin_ttfb,
    max(p95_origin_ttfb) as p95_origin_ttfb,
    round(sum(cnt_cache_miss) / greatest(sum(cnt_all), 1) * 100, 2) AS cache_miss_pct
FROM <project>.bot_agg_ua_hour
WHERE timestamp >= now() - INTERVAL 24 HOUR
GROUP BY bot_class
ORDER BY requests DESC

-- Top endpoints by origin cost proxy (p95 latency x volume).
SELECT
    request_path_norm,
    bot_class,
    sum(cnt_all) as requests,
    max(p95_origin_ttfb) as origin_p95_ms,
    requests * origin_p95_ms as origin_cost_score
FROM <project>.bot_agg_path_hour
WHERE timestamp >= now() - INTERVAL 24 HOUR
GROUP BY request_path_norm, bot_class
ORDER BY origin_cost_score DESC
LIMIT 20

-- Byte-level cost attribution requires raw fallback; response bytes are not in
-- current summaries.
SELECT
    is_bot_traffic,
    bot_class,
    sum(response_total_bytes) as total_bytes,
    round(total_bytes / 1073741824, 2) as total_gb,
    round(total_bytes / sum(total_bytes) OVER () * 100, 2) as pct_of_total
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 24 HOUR
GROUP BY is_bot_traffic, bot_class
ORDER BY total_bytes DESC

-- Cache impact of bot traffic from summaries.
SELECT
    is_bot_traffic,
    bot_class,
    sum(cnt_all) as requests,
    sum(cnt_cached) as cache_hits,
    round(cache_hits / greatest(requests, 1) * 100, 2) as hit_rate_pct,
    sum(cnt_cache_miss) as cache_misses
FROM <project>.bot_summary_hour
WHERE timestamp >= now() - INTERVAL 24 HOUR
GROUP BY is_bot_traffic, bot_class
ORDER BY requests DESC
```
