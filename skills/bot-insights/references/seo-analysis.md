# bot-insights — SEO Analysis Patterns

SEO analysis should start with crawler health and posture movement over time.
Use summaries for AI crawler share, bot class, error rates, cache miss rates,
resource categories, and paths. Fall back to request-level data for
`verified_bot_owner`, `bot_verification_tier`, exact `user_agent`, and
governance-surface inspection when those dimensions are required.
In SQL templates, replace `<posture_summary_day>` with `bi_summary_day` or an
equivalent metadata-confirmed `bot_summary_day`.

### Verified vs. Unverified Bots [SOC, SEO]

Verified owner and verification tier are not retained in the current summary
tables. Use this raw fallback with explicit time filters.

```sql
-- Verified bot owners
SELECT
    verified_bot_owner,
    bot_verification_tier,
    count() as requests,
    countIf(response_status_code >= '400') as errors
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 24 HOUR
  AND is_bot_traffic = true
  AND verified_bot_owner != ''
GROUP BY verified_bot_owner, bot_verification_tier
ORDER BY requests DESC

-- Unverified bots claiming to be known crawlers
SELECT
    user_agent,
    bot_category,
    bot_verification_tier,
    count() as requests,
    uniq(client_ip) as unique_ips
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 24 HOUR
  AND is_bot_traffic = true
  AND bot_verification_tier = ''
  AND user_agent ILIKE '%bot%'
GROUP BY user_agent, bot_category, bot_verification_tier
ORDER BY requests DESC
LIMIT 20
```

### Attack Data Analysis [SOC]

Attack payload details are not retained in the summary catalog. Use raw fallback
for payload inspection; use SIEM summaries for policy/action posture.

```sql
-- Requests with attack data by CDN
SELECT
    hdx_cdn,
    count() as total,
    countIf(attack_data != '') as with_attack_data,
    round(with_attack_data / total * 100, 2) as attack_pct
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 1 HOUR
GROUP BY hdx_cdn
ORDER BY with_attack_data DESC

-- Bot traffic with attack signatures by ASN
SELECT
    client_asn,
    client_country_iso_code,
    count() as requests,
    uniq(client_ip) as unique_ips,
    countIf(attack_data != '') as attack_requests
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND is_bot_traffic = true
GROUP BY client_asn, client_country_iso_code
ORDER BY attack_requests DESC
LIMIT 20
```

### Good Bot Governance [SEO]

Monitor legitimate crawlers and partner bots to ensure they can operate without
disruption — especially during security incidents or policy changes.

```sql
-- Summary-backed good bot health by day. Use raw fallback for verified owner.
SELECT
    timestamp,
    request_host,
    sum(cnt_all) AS requests,
    round(sum(cnt_4xx + cnt_5xx) / greatest(sum(cnt_all), 1) * 100, 2) AS error_rate_pct,
    round(sum(cnt_429) / greatest(sum(cnt_all), 1) * 100, 2) AS rate_limited_pct,
    max(p95_origin_ttfb) AS origin_p95_ms
FROM <project>.<posture_summary_day>
WHERE timestamp >= now() - INTERVAL 30 DAY
  AND bot_class = 'good'
GROUP BY timestamp, request_host
ORDER BY timestamp, request_host

-- Owner-specific health requires raw fallback.
SELECT
    verified_bot_owner,
    bot_category,
    count() AS requests,
    countIf(response_status_code >= '400') AS errors,
    round(errors / greatest(requests, 1) * 100, 2) AS error_rate_pct
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 24 HOUR
  AND bot_class = 'good'
  AND verified_bot_owner != ''
GROUP BY verified_bot_owner, bot_category
ORDER BY requests DESC

-- Summary-backed good bot access patterns by normalized path.
SELECT
    request_host,
    request_path_norm,
    sum(cnt_all) as crawl_hits,
    sum(cnt_2xx) as ok_2xx,
    sum(cnt_429) as rate_limited_429,
    sum(cnt_5xx) as server_errors_5xx
FROM <project>.bot_agg_path_day
WHERE timestamp >= now() - INTERVAL 24 HOUR
  AND bot_class = 'good'
GROUP BY request_host, request_path_norm
ORDER BY crawl_hits DESC
LIMIT 30

-- Good bot volume trending (detect drops that signal blocking or misconfiguration)
SELECT
    timestamp as hour,
    request_host,
    sum(cnt_all) as requests
FROM <project>.bot_agg_ua_hour
WHERE timestamp >= now() - INTERVAL 7 DAY
  AND bot_class = 'good'
GROUP BY hour, request_host
ORDER BY hour

-- Good bots being rate-limited or blocked (governance incident detection)
SELECT
    verified_bot_owner,
    response_status_code,
    count() as blocked_requests,
    uniq(client_ip) as unique_ips,
    min(timestamp) as first_seen,
    max(timestamp) as last_seen
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 24 HOUR
  AND bot_class = 'good'
  AND response_status_code IN ('403', '429', '503')
GROUP BY verified_bot_owner, response_status_code
ORDER BY blocked_requests DESC
```

### AI Crawler Monitoring [SEO]

Track AI-specific crawlers separately from traditional search bots. AI crawlers
split into three categories: scrapers (training data), assistants (answering
queries), and search (AI-powered search engines).

```sql
-- AI crawler movement by category from summaries.
SELECT
    ai_category,
    sum(cnt_all) as requests,
    sum(cnt_2xx) as ok_2xx,
    sum(cnt_429) as rate_limited_429,
    round(sum(cnt_cache_miss) / greatest(sum(cnt_all), 1) * 100, 2) AS cache_miss_pct
FROM <project>.<posture_summary_day>
WHERE timestamp >= now() - INTERVAL 24 HOUR
  AND ai_category != ''
GROUP BY ai_category
ORDER BY requests DESC

-- AI crawler volume trending over time
SELECT
    timestamp as hour,
    ai_category,
    sum(cnt_all) as requests
FROM <project>.bot_agg_traffic_hour
WHERE timestamp >= now() - INTERVAL 7 DAY
  AND ai_category != ''
GROUP BY hour, ai_category
ORDER BY hour

-- AI crawlers accessing governance surfaces (robots.txt, llms.txt)
SELECT
    ai_category,
    verified_bot_owner,
    request_path,
    count() as requests,
    countIf(response_status_code = '200') as ok_200
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 24 HOUR
  AND ai_category != ''
  AND (request_path LIKE '%robots.txt%' OR request_path LIKE '%llms.txt%')
GROUP BY ai_category, verified_bot_owner, request_path
ORDER BY requests DESC
```
