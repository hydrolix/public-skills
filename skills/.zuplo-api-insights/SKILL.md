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

## What This Bundle Contains

The Zuplo API Insights bundle ingests both Zuplo API gateway logs and Akamai CDN
edge logs into a unified `zuplo_gateway` table. This gives a combined view of API
traffic from the gateway layer (authentication, rate limiting, routing) and the edge
layer (caching, security rules, geographic distribution).

**Data sources**: Zuplo gateway telemetry, Akamai DS2 edge logs

**Tables**:
- `zuplo_gateway` — primary table (all request-level records)
- `zuplo_api_overview_summary_1m` — 1-minute rollup for volume, latency, auth/rate-limit outcomes
- `zuplo_api_abuse_summary_5m` — 5-minute rollup for abuse detection by ASN/country/policy
- `zuplo_api_security_correlation_summary_1h` — hourly rollup correlating edge security with gateway outcomes

See `references/schema.md` for the full column inventory and
`references/summary-tables.md` for summary table structure.

## Key Columns

### Gateway Dimensions

| Column | Description |
|--------|-------------|
| `timestamp` | Request timestamp |
| `route_group` | API route group (logical endpoint grouping) |
| `route_path` | Specific route pattern |
| `operation_id` | API operation identifier |
| `gateway_outcome` | Gateway processing result |
| `gateway_latency_ms` | Gateway processing time (Zuplo rows only) |
| `auth_outcome` | Authentication result |
| `rate_limit_outcome` | Rate limiting decision |
| `consumer_id` | Authenticated API consumer |
| `deployment_name` | Zuplo deployment identifier |

### Request/Response

| Column | Description |
|--------|-------------|
| `request_host` | API hostname |
| `request_method` | HTTP method |
| `request_path` | URL path |
| `response_status_code` | HTTP status code |
| `response_total_bytes` | Response size in bytes |
| `response_time_to_first_byte_ms` | TTFB (ms) |

### Client Identity

| Column | Description |
|--------|-------------|
| `client_ip` | Client IP address |
| `client_asn` | Client autonomous system |
| `client_country_iso_code` | Client country |
| `client_city` | Client city |
| `user_agent` | Raw user agent string |
| `user_agent_category` | Classified user agent type |
| `is_bot_traffic` | Bot classification flag |

### Akamai Edge Security

| Column | Description |
|--------|-------------|
| `hdx_cdn` | Data source identifier (akamai vs. zuplo) |
| `akamai_security_denied` | Whether Akamai blocked the request |
| `akamai_security_policy` | Active security policy |
| `akamai_security_deny_rule` | Rule that triggered the block |
| `akamai_security_deny_group` | Deny action group |
| `cache_was_cached` | Edge cache hit |
| `edge_pop` | Edge point of presence |

## Analysis Patterns

### API Traffic Overview

```sql
-- Request volume and latency by route group (use summary table)
SELECT
    route_group,
    sumMerge(request_count) as requests,
    avgMerge(avg_gateway_latency_ms) as avg_latency_ms,
    quantileTDigestMerge(0.95)(p95_gateway_latency_ms) as p95_latency_ms
FROM <project>.zuplo_api_overview_summary_1m
WHERE timestamp >= now() - INTERVAL 1 HOUR
GROUP BY route_group
ORDER BY requests DESC

-- Traffic over time by outcome
SELECT
    toStartOfMinute(timestamp) as minute,
    gateway_outcome,
    sumMerge(request_count) as requests
FROM <project>.zuplo_api_overview_summary_1m
WHERE timestamp >= now() - INTERVAL 1 HOUR
GROUP BY minute, gateway_outcome
ORDER BY minute
```

### Authentication Analysis

```sql
-- Auth outcome breakdown
SELECT
    auth_outcome,
    count() as requests,
    round(requests / sum(requests) OVER () * 100, 2) as pct
FROM <project>.zuplo_gateway
WHERE timestamp >= now() - INTERVAL 1 HOUR
GROUP BY auth_outcome
ORDER BY requests DESC

-- Failed auth by consumer
SELECT
    consumer_id,
    count() as failures
FROM <project>.zuplo_gateway
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND auth_outcome != 'success'
  AND auth_outcome != ''
GROUP BY consumer_id
ORDER BY failures DESC
LIMIT 20
```

### Rate Limiting

```sql
-- Rate-limited requests over time
SELECT
    toStartOfMinute(timestamp) as minute,
    rate_limit_outcome,
    sumMerge(request_count) as requests
FROM <project>.zuplo_api_overview_summary_1m
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND rate_limit_outcome != 'unknown'
GROUP BY minute, rate_limit_outcome
ORDER BY minute

-- Top consumers hitting rate limits
SELECT
    consumer_id,
    count() as rate_limited
FROM <project>.zuplo_gateway
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND rate_limit_outcome NOT IN ('', 'unknown')
GROUP BY consumer_id
ORDER BY rate_limited DESC
LIMIT 20
```

### Consumer Behavior

```sql
-- Top API consumers by volume
SELECT
    consumer_id,
    count() as requests,
    uniq(request_path) as unique_endpoints,
    avg(gateway_latency_ms) as avg_latency_ms
FROM <project>.zuplo_gateway
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND consumer_id != ''
GROUP BY consumer_id
ORDER BY requests DESC
LIMIT 20

-- Consumer activity over time
SELECT
    toStartOfMinute(timestamp) as minute,
    consumer_id,
    count() as requests
FROM <project>.zuplo_gateway
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND consumer_id = '<consumer_id>'
GROUP BY minute, consumer_id
ORDER BY minute
```

### Error Investigation

```sql
-- Error breakdown by status code and route
SELECT
    response_status_code,
    route_group,
    gateway_outcome,
    count() as errors
FROM <project>.zuplo_gateway
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND response_status_code >= '400'
GROUP BY response_status_code, route_group, gateway_outcome
ORDER BY errors DESC
LIMIT 20

-- 5xx errors with gateway context
SELECT
    timestamp,
    request_method,
    request_path,
    response_status_code,
    gateway_outcome,
    gateway_latency_ms,
    consumer_id
FROM <project>.zuplo_gateway
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND response_status_code >= '500'
ORDER BY timestamp DESC
LIMIT 50
```

### Abuse Detection

```sql
-- Suspicious traffic by ASN (use abuse summary table)
SELECT
    client_asn,
    client_country,
    sumMerge(request_count) as requests,
    sumMerge(unique_client_ip_count) as unique_ips,
    sumMerge(unique_consumer_count) as unique_consumers
FROM <project>.zuplo_api_abuse_summary_5m
WHERE timestamp >= now() - INTERVAL 1 HOUR
GROUP BY client_asn, client_country
ORDER BY requests DESC
LIMIT 20

-- Blocked requests by edge security policy
SELECT
    policy_name,
    akamai_rule_id,
    akamai_edge_action,
    sumMerge(request_count) as blocked
FROM <project>.zuplo_api_abuse_summary_5m
WHERE timestamp >= now() - INTERVAL 1 HOUR
  AND akamai_security_denied = true
GROUP BY policy_name, akamai_rule_id, akamai_edge_action
ORDER BY blocked DESC
```

### Edge vs. Gateway Security Correlation

```sql
-- Compare edge-blocked vs. gateway-served traffic by route
SELECT
    route_group,
    sumMergeIf(request_count, akamai_security_denied = true) as edge_blocked,
    sumMergeIf(request_count, akamai_security_denied = false) as edge_passed,
    round(edge_blocked / (edge_blocked + edge_passed) * 100, 2) as block_rate_pct
FROM <project>.zuplo_api_security_correlation_summary_1h
WHERE timestamp >= now() - INTERVAL 24 HOUR
GROUP BY route_group
ORDER BY edge_blocked DESC

-- False positive candidates: edge-blocked but gateway would have succeeded
SELECT
    route_group,
    policy_name,
    akamai_rule_id,
    gateway_outcome,
    sumMerge(request_count) as requests
FROM <project>.zuplo_api_security_correlation_summary_1h
WHERE timestamp >= now() - INTERVAL 24 HOUR
  AND akamai_security_denied = true
  AND gateway_outcome = 'success'
GROUP BY route_group, policy_name, akamai_rule_id, gateway_outcome
ORDER BY requests DESC
```

## Working With Summary Tables

Three summary tables at different granularities serve different analysis needs:

| Table | Granularity | Use For |
|-------|-------------|---------|
| `zuplo_api_overview_summary_1m` | 1 minute | Traffic volume, latency, auth/rate-limit outcomes |
| `zuplo_api_abuse_summary_5m` | 5 minutes | Abuse detection by ASN, country, security policy |
| `zuplo_api_security_correlation_summary_1h` | 1 hour | Edge security vs. gateway outcome correlation |

Summary tables use ClickHouse aggregate combiners. When re-aggregating:

| Primary table | Summary table equivalent |
|---------------|------------------------|
| `count()` | `sumMerge(request_count)` |
| `uniq(request_id)` | `sumMerge(unique_request_count)` |
| `uniq(consumer_id)` | `sumMerge(unique_consumer_count)` |
| `avg(gateway_latency_ms)` | `avgMerge(avg_gateway_latency_ms)` |
| `quantile(0.95)(gateway_latency_ms)` | `quantileTDigestMerge(0.95)(p95_gateway_latency_ms)` |

## Pitfalls

- **Dual data sources**: Rows come from both Zuplo gateway and Akamai DS2. Use
  `hdx_cdn` to distinguish. Akamai-specific columns (`akamai_*`) are null on
  Zuplo-only rows and vice versa for `gateway_latency_ms`.
- **response_status_code is a string**: Use string comparison (`>= '400'`) not
  numeric. Cast with `toUInt16OrZero(response_status_code)` if you need numeric
  operations.
- **gateway_latency_ms**: Only populated on Zuplo-sourced rows. The summary tables
  handle this correctly (quantileTDigest naturally excludes nulls).
- **Akamai security columns**: `akamai_security_policy`, `akamai_security_deny_rule`,
  etc. are only populated on Akamai DS2 rows. Filter by `hdx_cdn` or use the abuse/
  security summary tables which handle this.
- **`_raw` suffix columns**: These are unsuppressed raw variants of normalized fields.
  Use the normalized versions (without `_raw`) for analysis.
- **`zuplo_*` prefix columns**: Zuplo-specific geographic data from the gateway.
  Prefer the normalized `client_*` columns for cross-source analysis.
