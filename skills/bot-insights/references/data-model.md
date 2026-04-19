# bot-insights — Data Model

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

