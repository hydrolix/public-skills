# bot-insights — SOC Analysis Patterns

## Contents

- [Analysis Patterns](#analysis-patterns)
- [What Moved - Summary Delta](#what-moved--summary-delta-soc-director)
- [Mover Attribution](#mover-attribution-soc)
- [Request-Level Query - Newly Seen Entities](#request-level-query--newly-seen-entities-soc)
- [SOC Evidence - Behavioral Fingerprint](#soc-evidence--behavioral-fingerprint-soc)
- [Bot Classification Deep Dive](#bot-classification-deep-dive-soc)
- [Spoof Detection - Three-Signal Verification](#spoof-detection--three-signal-verification-soc)
- [Akamai vs. Hydrolix Signal Alignment](#akamai-vs-hydrolix-signal-alignment-soc-director)
- [Verified vs. Unverified Bots](#verified-vs-unverified-bots-soc-seo)
- [Attack Data Analysis](#attack-data-analysis-soc)
- [Bad Bot Behavior Patterns](#bad-bot-behavior-patterns-soc-edgeops)

## Analysis Patterns

SOC analysis remains available for short-window investigation, but the first
Bot Insights pass should still ask what posture moved and which retained
summary dimensions explain it. Use hour summaries for same-hour-yesterday or
same-weekday-hour-last-week comparisons, and minute summaries for detailed
policy-change timelines.
In SQL templates, replace `<posture_summary_hour>` with `bi_summary_hour` or an
`bi_summary_hour`.

When producing SOC scorecards, seed the entity population from
`bi_siem_policy_summary_*` on TrafficPeak/Akamai, or from another
metadata-confirmed SIEM summary table, not from an Edge/Ops or crawler top-N
list. Feed those rows to `scripts/scorecard.py` with
`analysis_domains: ["security_evidence"]` or `--domains security_evidence` so
only SOC-relevant evidence and missing inputs are evaluated.

For live `demo.trafficpeak.live` Akamai queries, prefer the dashboard field
names in `references/trafficpeak-demo.md`: `reqTimeSec`, `trafficCohort`,
`aiCategory`, `userAgentCategory`, `requestPathPattern`, `statusCode`, and
`cacheStatus` on posture summaries; `policyId`, `actionClass`, and `botType`
on SIEM policy summaries.

### What Moved — Summary Delta [SOC, Director+]

```sql
SELECT
  period,
  sum(cnt_all) AS requests,
  round(sumIf(cnt_all, is_bot_traffic = true) / greatest(sum(cnt_all), 1) * 100, 2) AS bot_share_pct,
  round(sumIf(cnt_all, bot_class = 'bad') / greatest(sum(cnt_all), 1) * 100, 2) AS bad_bot_share_pct,
  round(sum(cnt_429) / greatest(sum(cnt_all), 1) * 100, 2) AS rate_429_pct,
  round(sum(cnt_5xx) / greatest(sum(cnt_all), 1) * 100, 2) AS rate_5xx_pct,
  round(sum(cnt_cache_miss) / greatest(sum(cnt_all), 1) * 100, 2) AS cache_miss_pct
FROM (
  SELECT 'current' AS period, *
  FROM <project>.<posture_summary_hour>
  WHERE timestamp >= now() - INTERVAL 6 HOUR
  UNION ALL
  SELECT 'baseline' AS period, *
  FROM <project>.<posture_summary_hour>
  WHERE timestamp >= now() - INTERVAL 12 HOUR
    AND timestamp < now() - INTERVAL 6 HOUR
)
GROUP BY period
```

### Mover Attribution [SOC]

After confirming something changed, identify *who* is driving the change. Rank by
absolute delta (not total volume) to surface the entities that changed the most.
Use summaries first for retained dimensions such as ASN, bot class, path, host,
resource category, AI category, action, and policy.

```sql
SELECT
    client_asn AS value,
    sumIf(cnt_all, timestamp >= now() - INTERVAL 6 HOUR) AS current,
    sumIf(cnt_all, timestamp >= now() - INTERVAL 12 HOUR AND timestamp < now() - INTERVAL 6 HOUR) AS baseline,
    current - baseline AS absolute_delta,
    round(absolute_delta / greatest(baseline, 1) * 100, 2) AS pct_change
FROM <project>.<posture_summary_hour>
WHERE timestamp >= now() - INTERVAL 12 HOUR
GROUP BY client_asn
ORDER BY abs(absolute_delta) DESC
LIMIT 20

-- Top paths by absolute volume delta
SELECT
    request_path_norm AS value,
    sumIf(cnt_all, timestamp >= now() - INTERVAL 6 HOUR) AS current,
    sumIf(cnt_all, timestamp >= now() - INTERVAL 12 HOUR AND timestamp < now() - INTERVAL 6 HOUR) AS baseline,
    current - baseline AS absolute_delta
FROM <project>.bot_agg_path_hour
WHERE timestamp >= now() - INTERVAL 12 HOUR
GROUP BY request_path_norm
ORDER BY abs(absolute_delta) DESC
LIMIT 20
```

Use `scripts/compare_posture.py` to add contribution percentages and
`bot_mover_attribution.v1` interpretation constraints.

### Request-Level Query — Newly Seen Entities [SOC]

Newly seen exact IPs, user agents, and unretained dimensions require
request-level queries with narrow windows.

```sql

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
to understand behavior. Prefer summaries for retained fields, then fall back to
request-level records for method mix, exact user agent, headers, or attack
payload detail.

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
-- Summary-backed class movement. Use request-level query for bot_intent.
SELECT
    bot_class,
    sum(cnt_all) as requests,
    round(requests / sum(requests) OVER () * 100, 2) as pct
FROM <project>.bot_agg_ua_hour
WHERE timestamp >= now() - INTERVAL 24 HOUR
GROUP BY bot_class
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
`bot_confidence`, exact `user_agent`, and verification details are request-level
fields, so these are request-level queries.

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
