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

Use this skill to investigate bot behavior in the Hydrolix `bot-insights`
bundle without loading every query pattern up front. Start here to decide what
kind of question the user is asking, then load only the reference that contains
the relevant SQL and caveats.

This skill is compatible with Claude-style and Codex-style skill loading: it
uses standard `SKILL.md` frontmatter, relative markdown references, and no
agent-specific tool assumptions.

## When

Use this skill when the user asks about:

- Bot traffic share, bot scoring, bot class, confidence, intent, or producer.
- Verified and unverified bot ownership, crawler health, or AI crawler activity.
- Suspicious crawler spoofing, residential bot traffic, attack evidence, or
  Akamai SIEM bot/security enrichment.
- Bot-driven cache misses, query-string churn, origin load, latency, bandwidth,
  or rate-limiting impact.
- Before/after mitigation checks for blocks, cache-key changes, rate limits, or
  security policy changes.
- Bot posture across domains, hosts, ASNs, paths, countries, or CDN sources.

Do not use this skill for generic CDN traffic analysis unless bot fields are
central to the question; use `cdn-insights` for general cache, origin, traffic,
or error analysis.

## Why

The bundle combines normalized CDN access logs with bot enrichment. It is useful
because it lets an analyst connect automation identity to operational impact:
who the bot appears to be, whether that claim is credible, what it is doing, and
whether it is hurting cache, origin, SEO, or security posture.

The analysis should stay evidence-first. Do not classify traffic as malicious
from one signal alone. Prefer deltas, source attribution, behavior, and impact
over raw top-N volume.

## What

Primary table:

- `bot_detection`: request-level records with normalized CDN fields and bot
  intelligence. It has no summary tables, so use narrow time windows for heavy
  aggregations.

Key field groups:

- Time: `timestamp`
- Request: `request_host`, `request_path`, `request_method`,
  `request_query_string`
- Response/cache: `response_status_code`, `response_total_bytes`,
  `cache_was_cached`
- Client/CDN: `client_ip`, `client_asn`, `client_country_iso_code`, `edge_pop`,
  `hdx_cdn`
- Bot identity: `is_bot_traffic`, `bot_score`, `bot_category`, `bot_type`,
  `bot_class`, `bot_confidence`, `bot_intent`, `bot_verification_tier`,
  `verified_bot_owner`, `ai_category`
- Security evidence: `attack_data`, `asn_type`

## Progressive Disclosure

Do not read every reference at startup. Load the smallest relevant file:

- For table shape, sources, key fields, and personas, read
  [references/data-model.md](references/data-model.md).
- For full column inventory, flags, suppressed fields, and source coverage, read
  [references/schema.md](references/schema.md).
- For SOC/security investigations, deltas, movers, spoofing, attack evidence,
  classification, and bad bot behavior, read
  [references/soc-analysis.md](references/soc-analysis.md).
- For SEO, good bot governance, verified crawlers, and AI crawlers, read
  [references/seo-analysis.md](references/seo-analysis.md).
- For cache busting, query-string churn, origin impact, and bandwidth cost, read
  [references/edge-ops-analysis.md](references/edge-ops-analysis.md).
- For executive posture, multi-domain triage, and post-mitigation verification,
  read [references/executive-analysis.md](references/executive-analysis.md).
- Before finalizing a query or conclusion, scan
  [references/pitfalls.md](references/pitfalls.md).

## Triage Flow

1. Identify the persona and decision: SOC, SEO, Edge/Ops, or executive posture.
2. Preserve the requested time window, host/domain, path, ASN, owner, crawler, or
   mitigation time if supplied.
3. Start with a posture or delta query, not a raw top-N query, unless the user
   explicitly asks for inventory.
4. Attribute the change to concrete movers: ASN, path, host, bot owner, crawler,
   bot class, country, CDN, or status code.
5. Build evidence with at least two supporting dimensions before recommending
   action.
6. Keep time filters on every query; this bundle has no summary tables.

## Query Guardrails

- Always filter on `timestamp`.
- Use string comparisons for `response_status_code`, or cast explicitly with
  `toUInt32OrZero()` when numeric operations are needed.
- Prefer normalized fields over suppressed raw variants.
- Be explicit about `hdx_cdn` when comparing Akamai SIEM, Akamai DS2, and other
  CDN sources.
- Treat Akamai-provided bot fields and Hydrolix-derived bot fields as separate
  signals. Divergence is evidence to investigate, not an automatic error.
- For before/after checks, use the same baseline formula as the references:
  `(current - baseline) / greatest(baseline, 1) * 100`.
- Optionally use [scripts/compare_delta.py](scripts/compare_delta.py) to compute
  that formula from pasted current/baseline metric JSON. Use it only for numeric
  deltas; do not use it to classify bot intent or recommend action.

## Reference Map

- [references/data-model.md](references/data-model.md): bundle overview, key
  fields, and personas.
- [references/schema.md](references/schema.md): full schema with type, flags,
  and source coverage.
- [references/soc-analysis.md](references/soc-analysis.md): SOC and security
  query patterns.
- [references/seo-analysis.md](references/seo-analysis.md): crawler governance
  and AI crawler query patterns.
- [references/edge-ops-analysis.md](references/edge-ops-analysis.md): cache,
  origin, and bandwidth query patterns.
- [references/executive-analysis.md](references/executive-analysis.md):
  posture, multi-domain triage, and mitigation verification.
- [references/pitfalls.md](references/pitfalls.md): known schema and analysis
  footguns.
- [scripts/compare_delta.py](scripts/compare_delta.py): compute current versus
  baseline absolute and percentage deltas from simple metric JSON.
