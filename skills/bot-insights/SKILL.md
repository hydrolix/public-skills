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
the relevant summary table, SQL pattern, or caveat.

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
- Before/after checks for blocks, cache-key changes, rate limits, bot-control
  policies, or security policy changes.
- Bot posture across domains, hosts, ASNs, paths, countries, or CDN sources.
- Entity prioritization, deterministic scorecards, ranked investigation
  packets, or requests to rank risky/suspicious bot-related entities.

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

Primary request-level tables:

- `bot_detection`: request-level records with normalized CDN fields and bot
  intelligence.
- `bot_detection_siem`: SIEM-focused request-level records used by Akamai SIEM
  summaries.

Summary families:

- `bot_summary_*`: minute/hour/day posture summaries by host, CDN, bot class,
  AI category, bot flag, ASN, ASN type, resource category, and method.
- `bot_agg_*`: focused hourly and selected daily/minute summaries for host,
  ASN, path, resource, traffic, and bot class drilldowns.
- `bot_siem_*`: minute/hour/day summaries for action, policy, SIEM outcome,
  Akamai canonical class, and filter-aware views.

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
- For summary inventory, retained dimensions, and summary-first table
  selection, read [references/summary-tables.md](references/summary-tables.md).
- For quarter-over-quarter, month-over-month, week-over-week, year-over-year,
  seasonal, previous-window, and control-review baselines, read
  [references/baseline-comparison.md](references/baseline-comparison.md).
- For full column inventory, flags, suppressed fields, and source coverage, read
  [references/schema.md](references/schema.md).
- For SOC/security investigations, deltas, movers, spoofing, attack evidence,
  classification, and bad bot behavior, read
  [references/soc-analysis.md](references/soc-analysis.md).
- For SEO, good bot governance, verified crawlers, and AI crawlers, read
  [references/seo-analysis.md](references/seo-analysis.md).
- For structured cache-busting, query-string churn, cache-miss movement, or
  origin-impact detector output, read
  [references/cache-origin-impact.md](references/cache-origin-impact.md) first.
  For broader Edge/Ops cache, origin, and bandwidth query patterns, read
  [references/edge-ops-analysis.md](references/edge-ops-analysis.md).
- For deterministic entity scorecards that synthesize posture movement, mover
  attribution, SEO governance, Edge/Ops impact, and SIEM/security evidence into
  reusable investigation packets, read
  [references/scorecard-analysis.md](references/scorecard-analysis.md).
- For the advanced aggregate-delta attribution CLI, accepted public JSON row
  shapes, conservative confidence caps, and the boundary between legacy simple
  movers and `bot_attribution_report.v1`, read
  [references/advanced-attribution.md](references/advanced-attribution.md).
- For executive posture, multi-domain triage, and post-mitigation verification,
  read [references/executive-analysis.md](references/executive-analysis.md).
- For rendering saved Bot Insights artifacts into Markdown or self-contained
  HTML reports, read [references/reporting.md](references/reporting.md).
- Before finalizing a query or conclusion, scan
  [references/pitfalls.md](references/pitfalls.md).

## Triage Flow

1. Identify the persona and decision: SOC, SEO, Edge/Ops, or executive posture.
2. Preserve the requested time window, host/domain, path, ASN, owner, crawler, or
   mitigation time if supplied.
3. Start with the summary table whose retained dimensions fit the question.
4. For posture movement, prefer day summaries for QoQ/MoM/YoY, hour summaries
   for weekday/hour seasonality, and minute summaries for short policy-change
   detail.
5. Attribute the change to concrete movers: ASN, path, host, bot owner, crawler,
   bot class, country, CDN, or status code. Keep existing simple mover packets
   on [scripts/compare_posture.py](scripts/compare_posture.py); use
   [scripts/attribution.py](scripts/attribution.py) only when the user needs the
   advanced `bot_attribution_report.v1` aggregate-delta report.
6. Build evidence with at least two supporting dimensions before recommending
   action.
7. When the decision requires entity prioritization rather than another panel,
   produce scorecard-ready aggregate rows and run
   [scripts/scorecard.py](scripts/scorecard.py) to emit
   `bot_entity_scorecard.v1` packets plus a `bot_scorecard_index.v1`.
8. Fall back to request-level tables only when a required dimension is absent
   from summaries, and state the fallback reason.

## Query Guardrails

- Always filter on `timestamp`.
- Prefer summary tables when retained dimensions fit. Do not assume QoQ queries
  need monthly or quarterly summaries; benchmark daily summaries first.
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
- Use [scripts/compare_posture.py](scripts/compare_posture.py) for structured
  posture movement, legacy/simple mover attribution, and control-review JSON.
  It emits the existing `bot_posture_movement.v1`,
  `bot_mover_attribution.v1`, and `bot_control_review.v1` packet shapes. Keep
  simple posture and mover workflows here unless the user explicitly needs the
  advanced attribution report; the script accepts MCP query results, saved JSON,
  or pasted aggregate JSON only and does not query Hydrolix.
- Use [scripts/attribution.py](scripts/attribution.py) for advanced
  aggregate-delta attribution reports in `bot_attribution_report.v1`. The v1a
  standalone CLI accepts file, stdin, saved MCP result JSON, pasted JSON,
  wrapper objects, and list-of-dict aggregate rows; it does not query Hydrolix.
  Public JSON from this path is capped below high confidence, treats
  completeness and scorecard-safety fields as caller assertions, and exposes no
  scorecard export mode.
- Use [scripts/scorecard.py](scripts/scorecard.py) for deterministic
  scorecard artifacts after Hydrolix has produced entity-level aggregate rows.
  It accepts MCP query results, saved JSON, or pasted JSON only; it does not
  query Hydrolix. Missing feature inputs must remain `not_evaluated_features`,
  not implicit safe evidence.
- Use [scripts/cache_origin_impact.py](scripts/cache_origin_impact.py) for
  deterministic `cache_origin_impact_report.v1` artifacts after Hydrolix has
  produced path-grain aggregate rows. It accepts MCP query results, saved JSON,
  or pasted JSON only; it does not query Hydrolix, prove causality, or
  recommend mitigations.
- Use [scripts/render_report.py](scripts/render_report.py) to render saved Bot
  Insights artifacts into Markdown or self-contained HTML reports. It accepts
  existing artifact JSON only; it does not query Hydrolix, recompute scores, or
  infer missing evidence.
- Local scripts must not contain database clients, connection configuration, or
  credential handling. Use the Hydrolix MCP server or host Hydrolix query tool
  for all database access.

## Reference Map

- [references/data-model.md](references/data-model.md): bundle overview, key
  fields, and personas.
- [references/summary-tables.md](references/summary-tables.md): summary table
  inventory, retained dimensions, metrics, and raw fallback guidance.
- [references/baseline-comparison.md](references/baseline-comparison.md):
  comparison methods, granularity selection, confidence reasons, output schemas,
  and SQL templates.
- [references/schema.md](references/schema.md): full schema with type, flags,
  and source coverage.
- [references/soc-analysis.md](references/soc-analysis.md): SOC and security
  query patterns.
- [references/seo-analysis.md](references/seo-analysis.md): crawler governance
  and AI crawler query patterns.
- [references/cache-origin-impact.md](references/cache-origin-impact.md):
  structured `cache_origin_impact_report.v1` scope, SQL template guidance,
  standalone input/output examples, and detector boundaries.
- [references/edge-ops-analysis.md](references/edge-ops-analysis.md): cache,
  origin, and bandwidth query patterns.
- [references/scorecard-analysis.md](references/scorecard-analysis.md):
  deterministic entity scorecards, summary-first aggregate templates, SIEM
  enrichment, and reusable investigation packets.
- [references/advanced-attribution.md](references/advanced-attribution.md):
  advanced aggregate-delta attribution CLI, accepted v1a public input shapes,
  confidence caps, and the legacy/simple mover boundary.
- [references/executive-analysis.md](references/executive-analysis.md):
  posture, multi-domain triage, and mitigation verification.
- [references/reporting.md](references/reporting.md): renderer input grammar,
  supported report types, warning and evidence-limit expectations, and the
  artifact-only boundary.
- [references/pitfalls.md](references/pitfalls.md): known schema and analysis
  footguns.
- [scripts/compare_delta.py](scripts/compare_delta.py): compute current versus
  baseline absolute and percentage deltas from simple metric JSON.
- [scripts/compare_posture.py](scripts/compare_posture.py): emit structured
  Bot Insights posture movement, legacy/simple mover attribution, and
  control-review JSON from aggregate JSON.
- [scripts/attribution.py](scripts/attribution.py): emit conservative
  `bot_attribution_report.v1` aggregate-delta attribution reports from public
  aggregate JSON.
- [scripts/scorecard.py](scripts/scorecard.py): emit deterministic
  `bot_entity_scorecard.v1` and `bot_scorecard_index.v1` artifacts from
  entity-level aggregate JSON.
- [scripts/cache_origin_impact.py](scripts/cache_origin_impact.py): emit
  deterministic `cache_origin_impact_report.v1` artifacts from path-grain
  aggregate JSON.
- [scripts/render_report.py](scripts/render_report.py): render existing Bot
  Insights artifacts or `bot_report_input.v1` wrappers into Markdown or
  self-contained HTML reports.

## Script List

- `scripts/compare_delta.py`: simple current/baseline numeric deltas.
- `scripts/compare_posture.py`: posture movement, legacy/simple mover
  attribution, and control-review packets.
- `scripts/attribution.py`: advanced aggregate-delta attribution reports.
- `scripts/scorecard.py`: reusable entity scorecards and ranked scorecard
  index from aggregate JSON.
- `scripts/cache_origin_impact.py`: cache-busting and origin-impact candidate
  reports from already-aggregated path rows.
- `scripts/render_report.py`: dependency-free report renderer for existing
  Bot Insights artifacts.
