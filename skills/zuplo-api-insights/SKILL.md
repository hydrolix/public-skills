---
name: zuplo-api-insights
description: >
  Analyze API gateway traffic from a Hydrolix Zuplo API Insights bundle deployment.
  Use when investigating API request patterns, gateway latency, authentication outcomes,
  rate limiting, consumer behavior, route performance, or security events across
  Zuplo gateway and Akamai edge logs.
license: Apache-2.0
metadata:
  version: 1.0.0
  author: Hydrolix
  bundle: zuplo-api-insights
---

# Zuplo API Insights Analysis

Use this skill to analyze API gateway and edge-security traffic in the Hydrolix
Zuplo API Insights bundle while keeping the initial context small. Start here to
classify the task, then load only the reference that contains the detailed
schema, summary table, or SQL pattern you need.

This skill is compatible with Claude-style and Codex-style skill loading: it
uses standard `SKILL.md` frontmatter, relative markdown references, and no
agent-specific tool assumptions.

## When

Use this skill when the user asks about:

- API request volume, route groups, route paths, operations, or consumers.
- Gateway latency, route performance, or gateway outcomes.
- Authentication failures, rate limiting, throttling, or consumer behavior.
- 4xx/5xx errors with gateway context.
- Abuse detection by ASN, country, policy, route, auth outcome, or rate-limit
  outcome.
- Correlating Akamai edge security decisions with Zuplo gateway outcomes.
- Choosing between `zuplo_gateway` and the Zuplo summary tables.

Use `cdn-insights` instead for general CDN cache/origin analysis that does not
need API gateway or Zuplo-specific context.

## Why

The bundle combines Zuplo gateway telemetry with Akamai edge logs. It helps an
analyst connect API-layer outcomes, identity, route behavior, rate limiting, and
edge security into one investigation.

The safest workflow is to preserve source context. Some fields exist only on
Zuplo rows, while Akamai security fields exist only on Akamai DS2 rows. Use
summary tables when they retain the dimensions needed for the question.

## What

Primary table:

- `zuplo_gateway`: request-level gateway and edge records.

Summary tables:

- `zuplo_api_overview_summary_1m`: volume, latency, auth/rate-limit outcomes.
- `zuplo_api_abuse_summary_5m`: abuse detection by ASN, country, route, policy,
  and outcome.
- `zuplo_api_security_correlation_summary_1h`: edge-blocked versus
  gateway-served correlation.

Common field groups:

- API route: `route_group`, `route_path`, `operation_id`
- Gateway: `gateway_outcome`, `gateway_latency_ms`, `deployment_name`
- Auth/rate limiting: `auth_outcome`, `rate_limit_outcome`, `consumer_id`
- Request/response: `request_host`, `request_method`, `request_path`,
  `response_status_code`, `response_total_bytes`
- Client: `client_ip`, `client_asn`, `client_country_iso_code`,
  `client_country`, `user_agent`, `is_bot_traffic`
- Edge security: `akamai_security_denied`, `akamai_security_policy`,
  `akamai_security_deny_rule`, `akamai_security_deny_group`, `edge_pop`

## Progressive Disclosure

Do not read every reference at startup. Load the smallest relevant file:

- For bundle overview, key columns, source split, and table roles, read
  [references/data-model.md](references/data-model.md).
- For full column inventory, flags, suppressed fields, and source coverage, read
  [references/schema.md](references/schema.md).
- For API overview, auth, rate-limit, consumer, error, abuse, and correlation
  SQL patterns, read
  [references/analysis-patterns.md](references/analysis-patterns.md).
- For summary table dimensions, aggregate-state columns, and `-Merge` syntax,
  read [references/summary-tables.md](references/summary-tables.md).
- Before finalizing a query or conclusion, scan
  [references/pitfalls.md](references/pitfalls.md).

## Triage Flow

1. Identify the decision: API overview, latency, auth failure, rate limiting,
   consumer behavior, error investigation, abuse, or edge/gateway correlation.
2. Preserve route, consumer, status, policy, ASN, country, source, and time
   window constraints from the user.
3. Choose the summary table whose retained dimensions match the question.
4. Use `zuplo_gateway` for detail rows or dimensions absent from summaries.
5. Keep Zuplo-only and Akamai-only fields separate unless the query is
   intentionally correlating them.
6. Attribute changes by route, operation, consumer, ASN, country, policy,
   gateway outcome, auth outcome, rate-limit outcome, or edge decision.

## Query Guardrails

- Always filter on `timestamp`.
- `response_status_code` is a string. Compare as a string or cast explicitly
  with `toUInt16OrZero(response_status_code)`.
- `gateway_latency_ms` is populated on Zuplo-sourced rows only.
- Akamai security fields are populated on Akamai DS2 rows only.
- Prefer normalized `client_*` fields for cross-source analysis.
- Use `-Merge` combiners when querying summary tables, for example
  `sumMerge(request_count)`, `avgMerge(avg_gateway_latency_ms)`, or
  `quantileTDigestMerge(0.95)(p95_gateway_latency_ms)`.

## Reference Map

- [references/data-model.md](references/data-model.md): overview, key fields,
  source split, and table roles.
- [references/schema.md](references/schema.md): full schema with column type,
  flags, and sources.
- [references/analysis-patterns.md](references/analysis-patterns.md): detailed
  SQL patterns for common API investigations.
- [references/summary-tables.md](references/summary-tables.md): rollup
  dimensions, aggregate columns, and merge syntax.
- [references/pitfalls.md](references/pitfalls.md): common mistakes and
  source-specific caveats.
