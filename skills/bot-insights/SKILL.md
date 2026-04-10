---
name: bot-insights
description: >
  Analyze extended bot detection data with Akamai SIEM-enriched bot intelligence.
  Use when investigating bot scoring, classification confidence, bot intent,
  verified bot ownership, and attack data alongside CDN traffic patterns.
license: Apache-2.0
metadata:
  version: 1.0.0
  author: Hydrolix
  bundle: bot-insights
---

# Bot Insights Analysis

## What This Bundle Contains

The Bot Insights bundle extends CDN access log ingestion with rich bot
intelligence from Akamai SIEM and other CDN sources. Compared to the standard
bot-detection bundle, this variant includes bot scoring, classification
confidence, intent analysis, verified bot ownership, and attack data fields.

**Tables**:
- `bot_detection` — primary table (85 columns, all request-level records with
  bot classification and SIEM enrichment)

**Data sources**: Akamai DS2, Akamai SIEM, Akamai SIEM GZ, CloudFront Firehose,
Cloudflare, Fastly, Tencent, and other CDN sources (8 transforms)

See `references/schema.md` for the full column inventory.

## Key Columns

| Column | Description |
|--------|-------------|
| `timestamp` | Primary timestamp (epoch) |
| `is_bot_traffic` | Boolean bot classification |
| `bot_score` | Numeric bot confidence score (0-255) |
| `bot_category` | Bot category classification |
| `bot_type` | Bot type (e.g., search engine, scraper) |
| `bot_class` | Bot class (good, bad, unknown) |
| `bot_confidence` | Classification confidence level |
| `bot_intent` | Detected bot intent |
| `bot_verification_tier` | Verification tier for known bots |
| `verified_bot_owner` | Owner of verified bots (Google, Bing, etc.) |
| `user_agent_category` | User agent classification |
| `attack_data` | Akamai SIEM attack data payload |
| `request_host` | Hostname requested |
| `request_path` | URL path |
| `response_status_code` | HTTP status code |
| `response_total_bytes` | Response size in bytes |
| `cache_was_cached` | Whether response was served from cache |
| `client_ip` | Client IP address |
| `client_asn` | Client autonomous system number |
| `client_country_iso_code` | Client country |
| `edge_pop` | Edge point of presence |
| `hdx_cdn` | CDN provider |

## Personas

This bundle serves four distinct user roles. Each section below is tagged with
the personas it serves.

| Persona | Cares About |
|---------|-------------|
| **SOC / Security** | What changed, who is driving it, attack evidence, spoof detection |
| **SEO** | Good bot health, governance surfaces, AI crawler monitoring |
| **Edge / Ops** | Cache efficiency, origin load, querystring churn, bandwidth cost |
| **Director+ / Executive** | Posture scan, automation share, team routing, post-mitigation verification |

## Analysis Patterns

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

### Mover Attribution [SOC]

After confirming something changed, identify *who* is driving the change. Rank by
absolute delta (not total volume) to surface the entities that changed the most.

```sql
-- Top ASNs by absolute volume delta (current vs. baseline)
SELECT
    client_asn,
    countIf(timestamp >= now() - INTERVAL 6 HOUR) as current_requests,
    countIf(timestamp >= now() - INTERVAL 12 HOUR AND timestamp < now() - INTERVAL 6 HOUR) as baseline_requests,
    current_requests - baseline_requests as absolute_delta,
    round(absolute_delta / greatest(baseline_requests, 1) * 100, 2) as pct_change
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 12 HOUR
GROUP BY client_asn
ORDER BY abs(absolute_delta) DESC
LIMIT 20

-- Top paths by absolute volume delta
SELECT
    request_path,
    countIf(timestamp >= now() - INTERVAL 6 HOUR) as current_requests,
    countIf(timestamp >= now() - INTERVAL 12 HOUR AND timestamp < now() - INTERVAL 6 HOUR) as baseline_requests,
    current_requests - baseline_requests as absolute_delta
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 12 HOUR
GROUP BY request_path
ORDER BY abs(absolute_delta) DESC
LIMIT 20

-- Newly seen ASNs (absent from 7-day lookback, present now)
SELECT
    client_asn,
    count() as requests,
    uniq(client_ip) as unique_ips,
    min(timestamp) as first_seen
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 6 HOUR
  AND client_asn NOT IN (
    SELECT DISTINCT client_asn
    FROM <project>.bot_detection
    WHERE timestamp >= now() - INTERVAL 7 DAY
      AND timestamp < now() - INTERVAL 6 HOUR
  )
GROUP BY client_asn
ORDER BY requests DESC
LIMIT 20
```

### SOC Evidence — Behavioral Fingerprint [SOC]

Once a mover is identified (e.g., a specific ASN), build a behavioral profile
to confirm whether it is a targeted campaign.

```sql
-- Status code mix for a specific ASN
SELECT
    response_status_code,
    count() as requests,
    round(requests / sum(requests) OVER () * 100, 2) as pct
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 6 HOUR
  AND client_asn = '<suspect_asn>'
GROUP BY response_status_code
ORDER BY requests DESC

-- Method mix (scrapers are typically GET-only, no POST)
SELECT
    request_method,
    count() as requests,
    round(requests / sum(requests) OVER () * 100, 2) as pct
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 6 HOUR
  AND client_asn = '<suspect_asn>'
GROUP BY request_method
ORDER BY requests DESC

-- Endpoint concentration (scrapers target few paths)
SELECT
    request_path,
    count() as requests,
    round(requests / sum(requests) OVER () * 100, 2) as pct
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 6 HOUR
  AND client_asn = '<suspect_asn>'
GROUP BY request_path
ORDER BY requests DESC
LIMIT 10
```

### Bot Classification Deep Dive [SOC]

```sql
-- Bot traffic by class and intent
SELECT
    bot_class,
    bot_intent,
    count() as requests,
    round(requests / sum(requests) OVER () * 100, 2) as pct
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND is_bot_traffic = true
GROUP BY bot_class, bot_intent
ORDER BY requests DESC

-- Bot score distribution
SELECT
    multiIf(
        bot_score = 0, '0 (human)',
        bot_score <= 50, '1-50 (low)',
        bot_score <= 150, '51-150 (medium)',
        bot_score <= 200, '151-200 (high)',
        '201-255 (very high)'
    ) as score_bucket,
    count() as requests,
    round(requests / sum(requests) OVER () * 100, 2) as pct
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 1 HOUR
GROUP BY score_bucket
ORDER BY score_bucket
```

### Spoof Detection — Three-Signal Verification [SOC]

Uses `bot_confidence` to identify bots claiming to be legitimate crawlers but
originating from suspicious networks. The three signals are: UA pattern match,
vendor-published IP ranges, and ASN type.

```sql
-- Suspicious bots: UA claims bot, but source IP is residential
SELECT
    bot_category,
    bot_confidence,
    asn_type,
    count() as requests,
    uniq(client_ip) as unique_ips,
    uniq(client_asn) as unique_asns
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 24 HOUR
  AND is_bot_traffic = true
  AND bot_confidence = 'suspicious'
GROUP BY bot_category, bot_confidence, asn_type
ORDER BY requests DESC

-- Confidence level breakdown across all bot traffic
SELECT
    bot_confidence,
    count() as requests,
    round(requests / sum(requests) OVER () * 100, 2) as pct
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 24 HOUR
  AND is_bot_traffic = true
GROUP BY bot_confidence
ORDER BY requests DESC

-- Spoof candidates: claiming to be Googlebot/Bingbot but not from verified IPs
SELECT
    user_agent,
    client_asn,
    asn_type,
    bot_confidence,
    count() as requests,
    uniq(client_ip) as unique_ips
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 24 HOUR
  AND is_bot_traffic = true
  AND bot_confidence IN ('suspicious', 'plausible')
  AND (user_agent ILIKE '%googlebot%' OR user_agent ILIKE '%bingbot%')
GROUP BY user_agent, client_asn, asn_type, bot_confidence
ORDER BY requests DESC
LIMIT 20
```

### Akamai vs. Hydrolix Signal Alignment [SOC, Director+]

Compare vendor-provided classifications (Akamai Bot Manager) with Hydrolix's
independent three-signal classification. Divergences are investigative signals.

```sql
-- Agreement matrix: Akamai bot_category vs. Hydrolix bot_class
SELECT
    bot_category as akamai_category,
    bot_class as hydrolix_class,
    bot_confidence as hydrolix_confidence,
    count() as requests,
    round(requests / sum(requests) OVER () * 100, 2) as pct
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 24 HOUR
  AND is_bot_traffic = true
  AND hdx_cdn = 'akamai'
GROUP BY akamai_category, hydrolix_class, hydrolix_confidence
ORDER BY requests DESC

-- Divergence: Akamai says good bot, Hydrolix says suspicious
SELECT
    bot_category as akamai_category,
    bot_class as hydrolix_class,
    bot_confidence,
    verified_bot_owner,
    count() as requests,
    uniq(client_ip) as unique_ips
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 24 HOUR
  AND hdx_cdn = 'akamai'
  AND bot_confidence = 'suspicious'
GROUP BY akamai_category, hydrolix_class, bot_confidence, verified_bot_owner
ORDER BY requests DESC
LIMIT 20
```

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

### Bad Bot Behavior Patterns [SOC, Edge/Ops]

```sql
-- Bad bots targeting specific paths
SELECT
    request_path,
    count() as requests,
    uniq(client_ip) as unique_ips,
    avg(bot_score) as avg_bot_score
FROM <project>.bot_detection
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND bot_class = 'bad'
GROUP BY request_path
ORDER BY requests DESC
LIMIT 20
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

## Pitfalls

- **Bot score range**: `bot_score` is a uint8 (0-255). A score of 0 does not
  necessarily mean human — check `is_bot_traffic` for the boolean classification.
- **CDN-specific enrichment**: `bot_class`, `bot_confidence`, `bot_intent`, and
  `bot_verification_tier` come from 6 transforms (primarily Akamai SIEM). They
  may be empty for traffic from other CDN sources.
- **Akamai vs. Hydrolix columns**: Akamai-provided signals (`bot_score`,
  `bot_category`, `bot_type`) and Hydrolix-derived signals (`bot_class`,
  `bot_intent`, `bot_confidence`) are independent. Divergences between the two
  are investigative signals, not errors.
- **`user_agent_category`**: Only populated from Akamai SIEM transforms, not
  all 8 CDN sources. Use `bot_category` for broader coverage.
- **`response_status_code` is a string**: Use string comparison (e.g.,
  `>= '400'`) or cast with `toUInt32OrZero()`.
- **SIEM/DS2 deduplication**: The same request can appear in both SIEM and DS2
  feeds. Be explicit about which data source you are querying when counting.
  Filter by `hdx_cdn` to isolate a single source.
- **Suppressed columns**: `attack_data_raw`, `request_headers_raw`,
  `request_query_string`, and several others are suppressed. Use the normalized
  equivalents.
- **High volume**: This table can be very large. Always apply time filters.
  There are no summary tables in this bundle, so narrow your time windows for
  aggregations.
- **Delta baselines**: The demo and dashboards use `(current - baseline) / greatest(baseline, 1) * 100`
  as the standard delta formula. Use the same approach for consistency when writing
  custom queries.
