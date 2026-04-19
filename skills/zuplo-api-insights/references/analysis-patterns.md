# zuplo-api-insights — Analysis Patterns

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
  AND hdx_cdn = 'zuplo'
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
  AND hdx_cdn = 'zuplo'
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
