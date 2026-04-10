# zuplo-api-insights — Summary Tables

Pre-aggregated views for faster query performance at reduced granularity.

When querying summary tables, use `-Merge` aggregate combiners to re-aggregate
pre-computed values. For example, use `avgMerge(response_ttfb_ms)` instead of
`avg(response_ttfb_ms)`, and `quantilesMerge(0.5)(quantiles_response_ttfb_ms)`
instead of `quantile(0.5)(response_time_to_first_byte_ms)`.

## zuplo_api_overview_summary_1m

Parent table: `zuplo_gateway`

### Dimensions (GROUP BY)

- `timestamp`
- `route_group`
- `gateway_outcome`
- `auth_outcome`
- `rate_limit_outcome`
- `response_status_code`

### Aggregates

| Column | Expression |
|--------|------------|
| `request_count` | `count()` |
| `unique_request_count` | `uniq(request_id)` |
| `unique_consumer_count` | `uniq(consumer_id)` |
| `avg_gateway_latency_ms` | `avg(gateway_latency_ms)` |

### SQL

```sql
-- Summary: Zuplo API Overview
-- Granularity: 1 minute
-- Parent table: zuplo_gateway (via __TABLE_NAME__ placeholder)
-- Dimensions retained: route_group, gateway_outcome, auth_outcome, rate_limit_outcome, response_status_code
-- Serves analyses: volume, errors, latency, auth outcomes, throttling outcomes

SELECT
  toStartOfMinute(timestamp) AS timestamp,
  ifNull(nullIf(route_group, ''), 'unknown') AS route_group,
  ifNull(nullIf(gateway_outcome, ''), 'unknown') AS gateway_outcome,
  ifNull(nullIf(auth_outcome, ''), 'unknown') AS auth_outcome,
  ifNull(nullIf(rate_limit_outcome, ''), 'unknown') AS rate_limit_outcome,
  response_status_code,
  count() AS request_count,
  uniq(request_id) AS unique_request_count,
  uniq(consumer_id) AS unique_consumer_count,
  avg(gateway_latency_ms) AS avg_gateway_latency_ms,
  quantileTDigest(0.50)(gateway_latency_ms) AS p50_gateway_latency_ms,
  quantileTDigest(0.95)(gateway_latency_ms) AS p95_gateway_latency_ms,
  quantileTDigest(0.99)(gateway_latency_ms) AS p99_gateway_latency_ms
FROM __PROJECT_NAME__.__TABLE_NAME__
GROUP BY
  timestamp,
  route_group,
  gateway_outcome,
  auth_outcome,
  rate_limit_outcome,
  response_status_code
SETTINGS hdx_primary_key = 'timestamp'
```

## zuplo_api_abuse_summary_5m

Parent table: `zuplo_gateway`

### Dimensions (GROUP BY)

- `timestamp`
- `client_asn`
- `client_country`
- `route_group`
- `policy_name`
- `akamai_rule_id`
- `akamai_edge_action`
- `gateway_outcome`
- `auth_outcome`
- `rate_limit_outcome`
- `akamai_security_denied`

### Aggregates

| Column | Expression |
|--------|------------|
| `request_count` | `count()` |
| `unique_request_count` | `uniq(request_id)` |
| `unique_client_ip_count` | `uniq(client_ip)` |
| `unique_consumer_count` | `uniq(consumer_id)` |

### SQL

```sql
-- Summary: Zuplo API Abuse
-- Granularity: 5 minutes
-- Parent table: zuplo_gateway (via __TABLE_NAME__ placeholder)
-- Dimensions retained: client_asn, client_country, route_group,
--   akamai_security_policy, akamai_security_deny_rule, akamai_security_deny_group
-- Serves analyses: suspicious traffic attribution, blocked and throttled activity, edge policy evidence
-- Note: Akamai dimensions are populated on DS2 rows only (null on Zuplo-only rows)

SELECT
  toStartOfInterval(timestamp, INTERVAL 5 MINUTE) AS timestamp,
  ifNull(nullIf(client_asn, ''), 'unknown') AS client_asn,
  ifNull(nullIf(coalesce(client_country, client_country_iso_code), ''), 'unknown') AS client_country,
  ifNull(nullIf(route_group, ''), 'unknown') AS route_group,
  ifNull(nullIf(akamai_security_policy, ''), 'none') AS policy_name,
  ifNull(nullIf(akamai_security_deny_rule, ''), 'none') AS akamai_rule_id,
  ifNull(nullIf(akamai_security_deny_group, ''), 'none') AS akamai_edge_action,
  ifNull(nullIf(gateway_outcome, ''), 'unknown') AS gateway_outcome,
  ifNull(nullIf(auth_outcome, ''), 'unknown') AS auth_outcome,
  ifNull(nullIf(rate_limit_outcome, ''), 'unknown') AS rate_limit_outcome,
  akamai_security_denied,
  count() AS request_count,
  uniq(request_id) AS unique_request_count,
  uniq(client_ip) AS unique_client_ip_count,
  uniq(consumer_id) AS unique_consumer_count
FROM __PROJECT_NAME__.__TABLE_NAME__
GROUP BY
  timestamp,
  client_asn,
  client_country,
  route_group,
  policy_name,
  akamai_rule_id,
  akamai_edge_action,
  gateway_outcome,
  auth_outcome,
  rate_limit_outcome,
  akamai_security_denied
SETTINGS hdx_primary_key = 'timestamp'
```

## zuplo_api_security_correlation_summary_1h

Parent table: `zuplo_gateway`

### Dimensions (GROUP BY)

- `timestamp`
- `route_group`
- `policy_name`
- `akamai_rule_id`
- `akamai_edge_action`
- `gateway_outcome`
- `akamai_security_denied`

### Aggregates

| Column | Expression |
|--------|------------|
| `request_count` | `count()` |
| `unique_request_count` | `uniq(request_id)` |
| `unique_client_ip_count` | `uniq(client_ip)` |
| `unique_client_asn_count` | `uniq(client_asn)` |

### SQL

```sql
-- Summary: Zuplo API Security Correlation
-- Granularity: 1 hour
-- Parent table: zuplo_gateway (via __TABLE_NAME__ placeholder)
-- Dimensions retained: route_group, akamai_security_policy, akamai_security_deny_rule,
--   akamai_security_deny_group, gateway_outcome
-- Serves analyses: edge-blocked versus gateway-served comparisons and false-positive candidate review
-- Note: Akamai dimensions are populated on DS2 rows only (null on Zuplo-only rows)

SELECT
  toStartOfHour(timestamp) AS timestamp,
  ifNull(nullIf(route_group, ''), 'unknown') AS route_group,
  ifNull(nullIf(akamai_security_policy, ''), 'none') AS policy_name,
  ifNull(nullIf(akamai_security_deny_rule, ''), 'none') AS akamai_rule_id,
  ifNull(nullIf(akamai_security_deny_group, ''), 'none') AS akamai_edge_action,
  ifNull(nullIf(gateway_outcome, ''), 'unknown') AS gateway_outcome,
  akamai_security_denied,
  count() AS request_count,
  uniq(request_id) AS unique_request_count,
  uniq(client_ip) AS unique_client_ip_count,
  uniq(client_asn) AS unique_client_asn_count,
  -- gateway_latency_ms is sourced from Zuplo plugin telemetry only; DS2 rows
  -- have null gateway_latency_ms because Akamai does not measure gateway latency.
  -- quantileTDigest naturally excludes null values, so this metric reflects
  -- Zuplo-sourced measurements only. This is intentional and correct.
  quantileTDigest(0.95)(gateway_latency_ms) AS p95_gateway_latency_ms
FROM __PROJECT_NAME__.__TABLE_NAME__
GROUP BY
  timestamp,
  route_group,
  policy_name,
  akamai_rule_id,
  akamai_edge_action,
  gateway_outcome,
  akamai_security_denied
SETTINGS hdx_primary_key = 'timestamp'
```
