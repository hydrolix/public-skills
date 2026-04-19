# bot-insights — Executive Analysis Patterns

### What Changed — Delta Analysis [SOC, Director+]

The core investigation pattern: compare the current window to a baseline to detect
meaningful changes. This is the first thing a SOC operator or executive checks.

```sql
-- L0 posture check: volume, error rates, cache, origin latency vs. baseline
-- Compare last 6 hours to the 6 hours before that
SELECT
    'current' as period,
    count() as requests,
    round(countIf(response_status_code = '429') / count() * 100, 2) as rate_429_pct,
    round(countIf(response_status_code >= '500') / count() * 100, 2) as rate_5xx_pct,
    round(countIf(cache_was_cached = false) / count() * 100, 2) as cache_miss_pct,
    quantile(0.95)(origin_time_to_first_byte_ms) as origin_p95_ms
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 6 HOUR

UNION ALL

SELECT
    'baseline' as period,
    count() as requests,
    round(countIf(response_status_code = '429') / count() * 100, 2) as rate_429_pct,
    round(countIf(response_status_code >= '500') / count() * 100, 2) as rate_5xx_pct,
    round(countIf(cache_was_cached = false) / count() * 100, 2) as cache_miss_pct,
    quantile(0.95)(origin_time_to_first_byte_ms) as origin_p95_ms
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 12 HOUR
  AND timestamp < now() - INTERVAL 6 HOUR

-- Automation share: what percentage of traffic is bots?
SELECT
    round(countIf(is_bot_traffic = true) / count() * 100, 2) as bot_share_pct,
    count() as total_requests
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 6 HOUR
```


### Multi-Domain Triage [Director+]

For environments with multiple sites, compare posture across domains to route
investigation to the right team.

```sql
-- Posture by domain: bot share, error rate, cache miss rate
SELECT
    request_host,
    count() as requests,
    round(countIf(is_bot_traffic) / count() * 100, 2) as bot_share_pct,
    round(countIf(response_status_code = '429') / count() * 100, 2) as rate_429_pct,
    round(countIf(response_status_code >= '500') / count() * 100, 2) as rate_5xx_pct,
    round(countIf(cache_was_cached = false) / count() * 100, 2) as cache_miss_pct
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 6 HOUR
GROUP BY request_host
ORDER BY requests DESC
```

### Post-Mitigation Verification [Director+, SOC]

After deploying a mitigation (rate limiting, cache key normalization, ASN block),
verify that conditions improved using the same baseline logic.

```sql
-- Before vs. after mitigation: compare two 6-hour windows
-- Adjust the INTERVAL values to match your mitigation deployment time
SELECT
    'after_mitigation' as period,
    count() as requests,
    round(countIf(response_status_code = '429') / count() * 100, 2) as rate_429_pct,
    round(countIf(cache_was_cached = false) / count() * 100, 2) as cache_miss_pct,
    quantile(0.95)(origin_time_to_first_byte_ms) as origin_p95_ms,
    round(countIf(is_bot_traffic) / count() * 100, 2) as bot_share_pct
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 6 HOUR

UNION ALL

SELECT
    'before_mitigation' as period,
    count() as requests,
    round(countIf(response_status_code = '429') / count() * 100, 2) as rate_429_pct,
    round(countIf(cache_was_cached = false) / count() * 100, 2) as cache_miss_pct,
    quantile(0.95)(origin_time_to_first_byte_ms) as origin_p95_ms,
    round(countIf(is_bot_traffic) / count() * 100, 2) as bot_share_pct
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 12 HOUR
  AND timestamp < now() - INTERVAL 6 HOUR

-- Verify specific ASN was effectively mitigated
SELECT
    client_asn,
    countIf(timestamp >= now() - INTERVAL 6 HOUR) as after_requests,
    countIf(timestamp >= now() - INTERVAL 12 HOUR AND timestamp < now() - INTERVAL 6 HOUR) as before_requests,
    round((after_requests - before_requests) / greatest(before_requests, 1) * 100, 2) as change_pct
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 12 HOUR
  AND client_asn = '<mitigated_asn>'
GROUP BY client_asn
```

