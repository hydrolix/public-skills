# bot-insights — SOC Analysis Patterns

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

