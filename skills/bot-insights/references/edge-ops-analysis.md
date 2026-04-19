# bot-insights — Edge and Operations Analysis Patterns

### Cache-Busting and Querystring Churn Detection [Edge/Ops]

Bots that append unique query strings to every request defeat cache key matching,
causing artificial cache misses and origin overload.

```sql
-- Querystring diversity by path (high ratio = cache busting)
SELECT
    request_path,
    count() as requests,
    uniq(request_query_string) as unique_qs,
    round(unique_qs / requests, 4) as qs_diversity_ratio,
    countIf(cache_was_cached = false) as cache_misses,
    round(cache_misses / requests * 100, 2) as miss_rate_pct
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND is_bot_traffic = true
  AND request_query_string != ''
GROUP BY request_path
HAVING requests > 100
ORDER BY qs_diversity_ratio DESC
LIMIT 20

-- Querystring churn by ASN (attribute the cache busting to a source)
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
-- Origin latency by bot class (are bots degrading origin for humans?)
SELECT
    is_bot_traffic,
    bot_class,
    count() as requests,
    quantile(0.5)(origin_time_to_first_byte_ms) as origin_p50_ms,
    quantile(0.95)(origin_time_to_first_byte_ms) as origin_p95_ms,
    sum(response_total_bytes) as total_bytes
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND origin_time_to_first_byte_ms > 0
GROUP BY is_bot_traffic, bot_class
ORDER BY requests DESC

-- Top endpoints by origin cost (p95 latency x volume)
SELECT
    request_path,
    count() as requests,
    quantile(0.95)(origin_time_to_first_byte_ms) as origin_p95_ms,
    requests * origin_p95_ms as origin_cost_score,
    countIf(is_bot_traffic) as bot_requests,
    round(bot_requests / requests * 100, 2) as bot_pct
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND origin_time_to_first_byte_ms > 0
GROUP BY request_path
ORDER BY origin_cost_score DESC
LIMIT 20

-- Bandwidth cost attribution: bytes served to bots vs. humans
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

-- Cache impact of bot traffic
SELECT
    is_bot_traffic,
    bot_class,
    count() as requests,
    countIf(cache_was_cached = true) as cache_hits,
    round(cache_hits / requests * 100, 2) as hit_rate_pct,
    sum(response_total_bytes) as total_bytes
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 1 HOUR
GROUP BY is_bot_traffic, bot_class
ORDER BY requests DESC
```

