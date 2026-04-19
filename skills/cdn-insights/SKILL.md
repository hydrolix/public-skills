---
name: cdn-insights
description: >
  Analyze multi-CDN traffic data from a Hydrolix CDN Insights bundle deployment.
  Use when investigating request patterns, cache efficiency, origin health, edge
  performance, or error rates across Akamai, CloudFront, Fastly, Cloudflare, and
  other CDN sources.
license: Apache-2.0
metadata:
  version: 1.0.0
  author: Hydrolix
  bundle: cdn-insights
---

# CDN Insights Analysis

Use this skill to analyze normalized multi-CDN traffic in Hydrolix while keeping
the initial context small. Start here to identify the task, then load the
specific reference that contains the detailed schema, summary table, or SQL
pattern you need.

This skill is compatible with Claude-style and Codex-style skill loading: it
uses standard `SKILL.md` frontmatter, relative markdown references, and no
agent-specific tool assumptions.

## When

Use this skill when the user asks about:

- Request volume, traffic mix, host/path popularity, or CDN source mix.
- Cache hit ratio, cache misses, cache-key behavior, or origin offload.
- Origin health, TTFB/TTLB, edge performance, or latency percentiles.
- 4xx/5xx errors, status-code breakdowns, geographic distribution, or edge POPs.
- Comparing a current window to a baseline period.
- Choosing between the primary `cdn_insights` table and summary tables.

Use `bot-insights` instead when bot classification, crawler governance, spoofing,
or bot impact is the main question.

## Why

The CDN Insights bundle normalizes access logs from multiple CDN providers into
one query surface while preserving provider-specific fields. It helps an
analyst compare providers, find cache/origin problems, and explain traffic or
error changes without writing source-specific queries first.

The safest workflow is to start broad, then narrow by host, path, CDN, country,
ASN, edge POP, status code, or cache outcome. For large ranges, prefer summary
tables when their retained dimensions and aggregates fit the question.

## What

Primary table:

- `cdn_insights`: request-level normalized CDN traffic.

Summary tables:

- `mcdn_summary_min`: minute-granularity rollup.
- `mcdn_summary_hour`: hourly rollup.

Common normalized fields:

- Time: `timestamp`
- Request: `request_host`, `request_path`, `request_method`
- Response/cache: `response_status_code`, `response_total_bytes`,
  `cache_was_cached`
- Client/CDN: `client_country_iso_code`, `client_city`, `client_asn`,
  `edge_pop`, `hdx_cdn`
- Latency: `response_time_to_first_byte_ms`,
  `response_time_to_last_byte_ms`, `origin_time_to_first_byte_ms`,
  `origin_time_to_last_byte_ms`

## Progressive Disclosure

Do not read every reference at startup. Load the smallest relevant file:

- For bundle overview, discovery, key normalized columns, and source notes, read
  [references/data-model.md](references/data-model.md).
- For full column inventory, flags, suppressed fields, virtual fields, and
  provider-specific columns, read [references/schema.md](references/schema.md).
- For traffic, cache, origin, error, geography, and baseline SQL patterns, read
  [references/analysis-patterns.md](references/analysis-patterns.md).
- For summary table dimensions, aggregate-state columns, and `-Merge` syntax,
  read [references/summary-tables.md](references/summary-tables.md).
- For deployed SQL helper functions, read
  [references/shared-functions.md](references/shared-functions.md).
- Before finalizing a query or conclusion, scan
  [references/pitfalls.md](references/pitfalls.md).

## Triage Flow

1. Identify the user’s decision: traffic overview, cache efficiency, origin
   health, error investigation, geography/POP, or baseline comparison.
2. Confirm the project/table names and available time range.
3. Choose the narrowest useful time window.
4. Use a summary table for larger ranges when its dimensions and metrics fit.
5. Fall back to `cdn_insights` when the question needs a dimension or provider
   field absent from the summary table.
6. Attribute changes by concrete dimensions such as host, path, CDN, status,
   country, ASN, POP, or cache state.

## Query Guardrails

- Always filter on `timestamp`.
- Avoid `SELECT *`; this schema has many provider-specific columns.
- Use `response_total_bytes` for normalized bytes across CDNs.
- Filter by `hdx_cdn` before using provider-specific columns such as
  `akamai_*`, `cloudflare_*`, `cloudfront_*`, or `tencent_*`.
- Use `-Merge` combiners when querying summary tables, for example
  `sumMerge(cnt_all)` or `avgMerge(response_ttfb_ms)`.
- If a needed dimension is not retained in a summary table, query the primary
  table with a narrower time window.

## Reference Map

- [references/data-model.md](references/data-model.md): overview, discovery,
  and key normalized fields.
- [references/schema.md](references/schema.md): full schema with column type,
  flags, and sources.
- [references/analysis-patterns.md](references/analysis-patterns.md): detailed
  SQL patterns for common investigations.
- [references/summary-tables.md](references/summary-tables.md): rollup
  dimensions, aggregate columns, and merge syntax.
- [references/shared-functions.md](references/shared-functions.md): deployed
  helper SQL functions.
- [references/pitfalls.md](references/pitfalls.md): common mistakes and
  source-specific caveats.
