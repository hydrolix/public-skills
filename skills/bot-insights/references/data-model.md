# bot-insights — Data Model

## Contents

- [What This Bundle Contains](#what-this-bundle-contains)
- [Key Columns](#key-columns)
- [TrafficPeak Demo Shape](#trafficpeak-demo-shape)
- [Summary-First Analysis](#summary-first-analysis)
- [Personas](#personas)

## What This Bundle Contains

The Bot Insights bundle extends CDN access log ingestion with rich bot
intelligence from Akamai SIEM and other CDN sources. Compared to the standard
bot-detection bundle, this variant includes bot scoring, classification
confidence, intent analysis, verified bot ownership, and attack data fields.

**Deployed summary tables — the supported query surface**:
- `bi_summary_minute`, `bi_summary_hour`, `bi_summary_day` — posture summaries
  retaining `reqHost`, `asn`, `userAgentCategory`, `isBotTraffic`,
  `aiCategory`, `aiSource`, `trafficCohort`, `resourceCategory`, `reqMethod`,
  `cacheStatus`, `statusCode`, `requestPathPattern`, and `country`. Available
  on every Bot Insights cluster.
- `bi_siem_policy_summary_minute`, `bi_siem_policy_summary_hour`,
  `bi_siem_policy_summary_day` — with-SIEM policy/action summaries at
  minute/hour/day granularity. Available only on SIEM-enabled clusters such as
  `demo.trafficpeak.live` (not, for example, on `acme`).

**Not currently deployed** (kept here for design-intent reference only —
do not generate SQL against these):
- `bot_detection`, `bot_detection_siem` — request-level records. The Bot
  Insights summary tables aggregate from this request-level shape, but the
  request-level tables themselves are not deployed on observed clusters.
- `bot_agg_path_*`, `bot_agg_resource_*`, `bot_agg_ua_*`, `bot_agg_asn_hour`,
  `bot_agg_traffic_hour`, `bot_agg_hour` — focused aggregate families
  referenced by older skill iterations.

**Data sources**: Akamai DS2, Akamai SIEM, Akamai SIEM GZ, CloudFront Firehose,
Cloudflare, Fastly, Tencent, and other CDN sources (8 transforms)

See `references/schema.md` for the full column inventory.
See `references/summary-tables.md` for summary retained dimensions and metrics.

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

## TrafficPeak Demo Shape

The live `demo.trafficpeak.live` Akamai project is dashboarded from summary
tables. Posture queries should start with `akamai.bi_summary_minute`,
`akamai.bi_summary_hour`, or `akamai.bi_summary_day`. SIEM policy evidence
should start with `akamai.bi_siem_policy_summary_minute`,
`akamai.bi_siem_policy_summary_hour`, or
`akamai.bi_siem_policy_summary_day`.

Important source-style aliases in that project:

| Canonical concept | TrafficPeak summary field |
|-------------------|---------------------------|
| time | `reqTimeSec` on posture, `timestamp` on SIEM policy |
| request host | `reqHost` on posture, `host`/`reqHost` on SIEM policy |
| ASN | `asn` |
| bot boolean | `isBotTraffic` |
| AI category/source | `aiCategory`, `aiSource` |
| user-agent category | `userAgentCategory` |
| traffic cohort | `trafficCohort` (`Human`, `Bot`, `AI`) |
| path grouping | `requestPathPattern` |
| cache outcome | `cacheStatus` |
| status | `statusCode` on posture, `status`/`statusCode` on SIEM policy |
| SIEM policy/action/type | `policyId`, `actionClass`, `botType` |

## Summary-First Analysis

Use summaries for posture, health, and baseline movement whenever retained
dimensions fit the question. Daily summaries are the default for QoQ, MoM, YoY,
same-week-last-year, and executive posture. Hourly summaries are the default for
weekday/hour seasonality. Minute summaries are for short policy-change review or
incident detail.

Fields not retained in `bi_summary_*` or `bi_siem_policy_summary_*` —
`verified_bot_owner`, `bot_confidence`, `bot_intent`, canonical `bot_category`
or `bot_type`, `edge_pop`, exact payload `attack_data`, and exact
`user_agent` — would historically have come from request-level tables, but
those tables are not currently deployed. Surface the limitation in the
artifact rather than substituting a non-deployed table. TrafficPeak summary
fields available today include `userAgentCategory`, `trafficCohort`,
`aiCategory`, `aiSource`, `requestPathPattern`, numeric `statusCode`, and
`cacheStatus`.

## Personas

This bundle serves four distinct user roles. Each section below is tagged with
the personas it serves.

| Persona | Cares About |
|---------|-------------|
| **SOC / Security** | What changed, who is driving it, attack evidence, spoof detection |
| **SEO** | Good bot health, governance surfaces, AI crawler monitoring |
| **Edge / Ops** | Cache efficiency, origin load, querystring churn, bandwidth cost |
| **Director+ / Executive** | Posture scan, automation share, team routing, post-mitigation verification |
