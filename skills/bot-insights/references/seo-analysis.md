# bot-insights — SEO Analysis Patterns

### Verified vs. Unverified Bots [SOC, SEO]

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
-- Good bot health dashboard: volume, errors, and latency
SELECT
    verified_bot_owner,
    bot_category,
    count() as requests,
    countIf(response_status_code >= '400') as errors,
    round(errors / requests * 100, 2) as error_rate_pct,
    avg(response_time_to_first_byte_ms) as avg_ttfb_ms
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 24 HOUR
  AND bot_class = 'good'
GROUP BY verified_bot_owner, bot_category
ORDER BY requests DESC

-- Good bot access patterns by path (SEO visibility check)
SELECT
    request_host,
    request_path,
    verified_bot_owner,
    count() as crawl_hits,
    countIf(response_status_code = '200') as ok_200,
    countIf(response_status_code = '404') as not_found_404,
    countIf(response_status_code = '429') as rate_limited_429,
    countIf(response_status_code >= '500') as server_errors_5xx
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 24 HOUR
  AND bot_class = 'good'
GROUP BY request_host, request_path, verified_bot_owner
ORDER BY crawl_hits DESC
LIMIT 30

-- Good bot volume trending (detect drops that signal blocking or misconfiguration)
SELECT
    toStartOfHour(timestamp) as hour,
    verified_bot_owner,
    count() as requests
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 7 DAY
  AND bot_class = 'good'
  AND verified_bot_owner != ''
GROUP BY hour, verified_bot_owner
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
-- AI crawler volume by category
SELECT
    ai_category,
    verified_bot_owner,
    count() as requests,
    countIf(response_status_code = '200') as ok_200,
    countIf(response_status_code = '429') as rate_limited_429,
    sum(response_total_bytes) as total_bytes
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 24 HOUR
  AND ai_category != ''
GROUP BY ai_category, verified_bot_owner
ORDER BY requests DESC

-- AI crawler volume trending over time
SELECT
    toStartOfHour(timestamp) as hour,
    ai_category,
    count() as requests
FROM <project>.bot_detection
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

