# bot-insights — SEO Analysis Patterns

SEO analysis runs against `bi_summary_*` for AI crawler share, user-agent
category, error rates, and cache miss rates. Request-level dimensions
(`verified_bot_owner`, `bot_verification_tier`, exact `user_agent`,
governance-surface inspection) historically came from `bot_detection`; that
table is **not currently deployed** on production clusters. Apply the
deployment-availability rule (SKILL.md) when a question depends on a
request-level dimension.

SQL examples use `bi_summary_day` directly for the Akamai/TrafficPeak
project. On other clusters, confirm the equivalent posture-summary table
name in metadata before adapting.

## Contents

- [Verified vs. Unverified Bots](#verified-vs-unverified-bots-soc-seo)
- [Attack Data Analysis](#attack-data-analysis-soc)
- [Good Bot Governance](#good-bot-governance-seo)
- [AI Crawler Monitoring](#ai-crawler-monitoring-seo)

### Verified vs. Unverified Bots [SOC, SEO]

`verified_bot_owner` and `bot_verification_tier` are request-level dimensions
not retained in deployed summaries. The request-level `bot_detection` table is
not currently deployed; verified-owner audits are not supported at the
deployed grain. State the limitation in the artifact when a SEO investigation
depends on this dimension.

Deployed surfaces can still show coarse crawler health: filter `bi_summary_*`
on `userAgentCategory` cohorts (for example `Search Engine Crawler`) and on
`resourceCategory` crawler-file values (`robots.txt`, `sitemap.xml`) to find
aggregate crawler-health movement. Exact ownership of those bots is not
surfaced.

### Attack Data Analysis [SOC]

Attack payload (`attack_data`) is a request-level field that depended on
`bot_detection`, which is not currently deployed. Use the SIEM policy
summaries (`bi_siem_policy_summary_*`) for policy/action posture and SIEM
blocked/auth-fail evidence; payload-level forensics is not supported at the
deployed grain.

### Good Bot Governance [SEO]

For the predefined `crawler_governance` report these templates feed, prefer
the script-orchestrated path documented in
[`references/reporting.md`](reporting.md) — `bot_insights_report.py --report
crawler_governance` runs the query directly when local credentials resolve
and emits a handoff packet otherwise. Run the templates directly via Hydrolix
MCP only for exploratory crawler analysis outside a predefined report. See
[SKILL.md "Data Firewall"](../SKILL.md#data-firewall).

Monitor legitimate crawlers and partner bots to ensure they can operate without
disruption — especially during security incidents or policy changes.

```sql
-- Summary-backed crawler health by day on the deployed posture surface.
-- Filter on userAgentCategory (deployed posture summaries do not retain a
-- bot_class column).
SELECT
    reqTimeSec,
    reqHost,
    sum(cnt_all) AS requests,
    round(sum(cnt_4xx + cnt_5xx) / greatest(sum(cnt_all), 1) * 100, 2) AS error_rate_pct,
    round(sum(cnt_429) / greatest(sum(cnt_all), 1) * 100, 2) AS rate_limited_pct,
    max(p95_origin_ttfb) AS origin_p95_ms
FROM <project>.bi_summary_day
WHERE reqTimeSec >= now() - INTERVAL 30 DAY
  AND userAgentCategory = 'Search Engine Crawler'
GROUP BY reqTimeSec, reqHost
ORDER BY reqTimeSec, reqHost

-- Crawler volume trending by host (drops signal blocking or misconfiguration).
SELECT
    reqTimeSec AS hour,
    reqHost,
    sum(cnt_all) AS requests
FROM <project>.bi_summary_hour
WHERE reqTimeSec >= now() - INTERVAL 7 DAY
  AND userAgentCategory = 'Search Engine Crawler'
GROUP BY hour, reqHost
ORDER BY hour
```

Confirm the exact `userAgentCategory` value in metadata for the target
cluster; the literal `'Search Engine Crawler'` shown above matches the
TrafficPeak Akamai project.

Owner-specific crawler health, per-path crawler access patterns, and exact
status-code-by-owner investigations all required request-level fields
(`verified_bot_owner`, `request_path`, exact `response_status_code`) that are
not retained in deployed summaries. Apply the deployment-availability rule
(SKILL.md).

### AI Crawler Monitoring [SEO]

Track AI-specific crawlers separately from traditional search bots. AI crawlers
split into three categories: scrapers (training data), assistants (answering
queries), and search (AI-powered search engines).

```sql
-- AI crawler movement by category from deployed posture summaries.
SELECT
    aiCategory,
    sum(cnt_all) AS requests,
    sum(cnt_2xx) AS ok_2xx,
    sum(cnt_429) AS rate_limited_429,
    round(sum(cnt_cache_miss) / greatest(sum(cnt_all), 1) * 100, 2) AS cache_miss_pct
FROM <project>.bi_summary_day
WHERE reqTimeSec >= now() - INTERVAL 24 HOUR
  AND aiCategory != ''
GROUP BY aiCategory
ORDER BY requests DESC

-- AI crawler volume trending over time from the hour-grain posture summary.
SELECT
    reqTimeSec AS hour,
    aiCategory,
    sum(cnt_all) AS requests
FROM <project>.bi_summary_hour
WHERE reqTimeSec >= now() - INTERVAL 7 DAY
  AND aiCategory != ''
GROUP BY hour, aiCategory
ORDER BY hour
```

Governance-surface inspection (which AI crawlers actually hit `robots.txt`,
`llms.txt`, or `ai.txt`) is a request-path-grain question. The deployed
posture summary retains `requestPathPattern`, which buckets traffic into
broad categories rather than exact paths; exact-path governance audits depend
on request-level `bot_detection` and are not supported at the deployed grain.
