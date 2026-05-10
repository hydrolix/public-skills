# bot-insights — SOC Analysis Patterns

## Contents

- [Analysis Patterns](#analysis-patterns)
- [What Moved - Summary Delta](#what-moved--summary-delta-soc-director)
- [Mover Attribution](#mover-attribution-soc)
- [Newly Seen Entities](#newly-seen-entities-soc)
- [SOC Evidence - Behavioral Fingerprint](#soc-evidence--behavioral-fingerprint-soc)
- [Bot Classification Deep Dive](#bot-classification-deep-dive-soc)
- [Spoof Detection - Three-Signal Verification](#spoof-detection--three-signal-verification-soc)
- [Akamai vs. Hydrolix Signal Alignment](#akamai-vs-hydrolix-signal-alignment-soc-director)
- [Verified vs. Unverified Bots](#verified-vs-unverified-bots-soc-seo)
- [Attack Data Analysis](#attack-data-analysis-soc)
- [Bad Bot Behavior Patterns](#bad-bot-behavior-patterns-soc-edgeops)

## Analysis Patterns

For predefined SOC reports (`soc_triage`), prefer the script-orchestrated
capture path documented in [reporting.md](reporting.md): `bot_insights_report.py
--report soc_triage` runs the SQL directly when local Hydrolix credentials
resolve and emits a `bot_hydrolix_mcp_query_request.v1` handoff packet
otherwise. The SQL templates below are intended for that capture path or for
exploratory SOC investigation outside a predefined report — see
[SKILL.md "Data Firewall"](../SKILL.md#data-firewall) for the rule on when MCP
is forbidden.

SOC analysis runs against `bi_summary_*` and (on SIEM-enabled clusters)
`bi_siem_policy_summary_*`. The request-level `bot_detection` and
`bot_detection_siem` tables and focused aggregates (`bot_agg_path_*`,
`bot_agg_ua_*`) are **not currently deployed** on production clusters; see
[data-model.md](data-model.md). Request-level dimensions — exact `user_agent`,
exact `client_ip`, `bot_confidence`, `bot_intent`, attack payload, exact
status code, exact method, exact `request_path` — should be surfaced as
limitations in the artifact rather than queried against non-deployed tables.

Use hour summaries for same-hour-yesterday or same-weekday-hour-last-week
comparisons, and minute summaries for detailed policy-change timelines. In
SQL templates, replace `<posture_summary_hour>` with `bi_summary_hour` (or the
metadata-confirmed equivalent for the target cluster).

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

-- Top request-path patterns by absolute volume delta from the deployed posture summary.
SELECT
    requestPathPattern AS value,
    sumIf(cnt_all, reqTimeSec >= now() - INTERVAL 6 HOUR) AS current,
    sumIf(cnt_all, reqTimeSec >= now() - INTERVAL 12 HOUR AND reqTimeSec < now() - INTERVAL 6 HOUR) AS baseline,
    current - baseline AS absolute_delta
FROM <project>.bi_summary_hour
WHERE reqTimeSec >= now() - INTERVAL 12 HOUR
GROUP BY requestPathPattern
ORDER BY abs(absolute_delta) DESC
LIMIT 20
```

Exact-path mover attribution (`request_path` rather than the
`requestPathPattern` bucket) is a request-level dimension; surface that
limitation in the artifact when needed.

Use `scripts/compare_posture.py` to add contribution percentages and
`bot_mover_attribution.v1` interpretation constraints.

### Newly Seen Entities [SOC]

Newly seen ASNs are visible at the deployed grain because `asn` is a retained
posture-summary dimension. For exact `client_ip`, exact `user_agent`, and
other request-level identity dimensions, the request-level `bot_detection`
table is not currently deployed; surface that as a limitation.

```sql
-- Newly seen ASNs from the deployed posture summary
-- (absent from 7-day lookback, present in the last 6 hours).
SELECT
    asn,
    sum(cnt_all) AS requests,
    min(reqTimeSec) AS first_seen
FROM <project>.bi_summary_hour
WHERE reqTimeSec >= now() - INTERVAL 6 HOUR
  AND asn NOT IN (
    SELECT DISTINCT asn
    FROM <project>.bi_summary_hour
    WHERE reqTimeSec >= now() - INTERVAL 7 DAY
      AND reqTimeSec < now() - INTERVAL 6 HOUR
  )
GROUP BY asn
ORDER BY requests DESC
LIMIT 20
```

### SOC Evidence — Behavioral Fingerprint [SOC]

Once a mover is identified (e.g., a specific ASN), profile its behavior on
retained summary dimensions: status-code mix (`statusCode`), cache outcome
(`cacheStatus`), method (`reqMethod`), and bucketed path (`requestPathPattern`).

```sql
-- Status-code mix for a specific ASN from the deployed posture summary.
SELECT
    statusCode,
    sum(cnt_all) AS requests,
    round(sum(cnt_all) / sum(sum(cnt_all)) OVER () * 100, 2) AS pct
FROM <project>.bi_summary_hour
WHERE reqTimeSec >= now() - INTERVAL 6 HOUR
  AND asn = '<suspect_asn>'
GROUP BY statusCode
ORDER BY requests DESC

-- Method mix for the same ASN.
SELECT
    reqMethod,
    sum(cnt_all) AS requests,
    round(sum(cnt_all) / sum(sum(cnt_all)) OVER () * 100, 2) AS pct
FROM <project>.bi_summary_hour
WHERE reqTimeSec >= now() - INTERVAL 6 HOUR
  AND asn = '<suspect_asn>'
GROUP BY reqMethod
ORDER BY requests DESC
```

Exact-path endpoint concentration (`request_path` rather than the
`requestPathPattern` bucket), exact `user_agent`, header mix, and attack
payload detail are request-level dimensions; surface those as limitations.

### Bot Classification Deep Dive [SOC]

```sql
-- Bot class movement from the deployed posture summary.
SELECT
    bot_class,
    sum(cnt_all) AS requests,
    round(sum(cnt_all) / sum(sum(cnt_all)) OVER () * 100, 2) AS pct
FROM <project>.bi_summary_hour
WHERE reqTimeSec >= now() - INTERVAL 24 HOUR
GROUP BY bot_class
ORDER BY requests DESC
```

`bot_score` distribution, `bot_confidence`, `bot_intent`, and canonical
`bot_category`/`bot_type` are request-level fields not retained in deployed
summaries. State that limitation when an investigation needs them.

### Spoof Detection — Three-Signal Verification [SOC]

`bot_confidence`, exact `user_agent`, exact `client_ip`, and verification tier
are all request-level dimensions; the request-level `bot_detection` table is
not currently deployed, so three-signal spoof detection is not supported at
the deployed grain. Use SIEM evidence (`bi_siem_policy_summary_*`,
`actionClass`, `botType`, `policyId`) when the cluster has SIEM data, and
surface the missing request-level surface as a limitation otherwise.

### Akamai vs. Hydrolix Signal Alignment [SOC, Director+]

Vendor-vs-Hydrolix classification comparisons depend on per-request
`bot_category`, `bot_class`, `bot_confidence`, `verified_bot_owner`, and
`hdx_cdn`. Those fields require the request-level `bot_detection` table,
which is not currently deployed; signal-alignment analysis is not supported
at the deployed grain.

### Verified vs. Unverified Bots [SOC, SEO]

`verified_bot_owner` and `bot_verification_tier` are request-level
dimensions; see [seo-analysis.md](seo-analysis.md#verified-vs-unverified-bots-soc-seo)
for the deployment-state note.

### Attack Data Analysis [SOC]

`attack_data` is a request-level payload field; the request-level
`bot_detection` table is not currently deployed. Use
`bi_siem_policy_summary_*` for policy/action posture (blocked requests, auth
failures, SIEM bot type) on SIEM-enabled clusters; payload-level inspection
is not supported at the deployed grain.

### Bad Bot Behavior Patterns [SOC, Edge/Ops]

```sql
-- Bad-bot host concentration from the deployed posture summary.
SELECT
    reqHost,
    sumIf(cnt_all, bot_class = 'bad') AS bad_requests,
    sum(cnt_all) AS requests,
    round(sumIf(cnt_all, bot_class = 'bad') / greatest(sum(cnt_all), 1) * 100, 2) AS bad_bot_share_pct,
    avgIf(avg_bot_score, bot_class = 'bad') AS avg_bot_score_bad
FROM <project>.bi_summary_hour
WHERE reqTimeSec >= now() - INTERVAL 1 HOUR
GROUP BY reqHost
ORDER BY bad_requests DESC
LIMIT 20
```

Exact-path targeting (`request_path`) and unique client-IP counts are
request-level dimensions; surface those as limitations when needed.
