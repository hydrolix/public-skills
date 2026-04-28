# bot-insights — Data Model

## What This Bundle Contains

The Bot Insights bundle extends CDN access log ingestion with rich bot
intelligence from Akamai SIEM and other CDN sources. Compared to the standard
bot-detection bundle, this variant includes bot scoring, classification
confidence, intent analysis, verified bot ownership, and attack data fields.

**Request-level tables**:
- `bot_detection` — primary table (85 columns, request-level records with bot
  classification and SIEM enrichment)
- `bot_detection_siem` — SIEM-oriented request-level records used by Akamai
  SIEM summaries

**Summary tables**:
- `bi_summary_minute`, `bi_summary_hour`, `bi_summary_day` and equivalent
  `bot_summary_*` tables — posture summaries by host, CDN, bot class, AI
  category, bot flag, ASN, ASN type, resource category, and method.
- `bot_agg_*` — focused host, ASN, path, resource, traffic, and bot-class
  summaries at hour granularity, with selected day/minute variants.
- `bi_siem_summary_*` and `bot_siem_*` — action, policy, filter, and Akamai
  canonical class summaries at minute/hour/day granularity.

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

## Summary-First Analysis

Use summaries for posture, health, and baseline movement whenever retained
dimensions fit the question. Daily summaries are the default for QoQ, MoM, YoY,
same-week-last-year, and executive posture. Hourly summaries are the default for
weekday/hour seasonality. Minute summaries are for short policy-change review or
incident detail.

Raw request-level fallback is required for fields that are not retained in the
current summary catalog, such as `verified_bot_owner`, `bot_confidence`,
`bot_intent`, `bot_category`, `bot_type`, `client_country_iso_code`, `edge_pop`,
exact `response_status_code`, `attack_data`, `user_agent`, and
`user_agent_category`.

## Personas

This bundle serves four distinct user roles. Each section below is tagged with
the personas it serves.

| Persona | Cares About |
|---------|-------------|
| **SOC / Security** | What changed, who is driving it, attack evidence, spoof detection |
| **SEO** | Good bot health, governance surfaces, AI crawler monitoring |
| **Edge / Ops** | Cache efficiency, origin load, querystring churn, bandwidth cost |
| **Director+ / Executive** | Posture scan, automation share, team routing, post-mitigation verification |
