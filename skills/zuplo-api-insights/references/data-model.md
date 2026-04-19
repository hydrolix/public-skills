# zuplo-api-insights — Data Model

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

