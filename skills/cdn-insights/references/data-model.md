# cdn-insights — Data Model

## What This Bundle Contains

The CDN Insights bundle ingests access logs from multiple CDN providers into a
unified `cdn_insights` table. Each CDN source has its own transform that normalizes
fields into a common schema while preserving provider-specific columns.

**Supported CDN sources**: Akamai (DS2), CloudFront (Firehose), Fastly, Cloudflare,
Tencent, BytePlus, CacheFly, Google Media CDN, Imperva, IOriver, Varnish

**Tables**:
- `cdn_insights` — primary table (all request-level records)
- `mcdn_summary_min` — minute-granularity pre-aggregated rollup
- `mcdn_summary_hour` — hourly pre-aggregated rollup

See `references/schema.md` for the full column inventory and
`references/summary-tables.md` for summary table structure.

## Discovery

Start by identifying what's available in the deployment:

```sql
-- List available tables in the project
-- Use list_tables to find the project and table names
-- The primary table is typically named cdn_insights

-- Check the time range of available data
SELECT min(timestamp), max(timestamp), count()
FROM <project>.<table>

-- See which CDN sources are present
SELECT hdx_cdn, count() as requests
FROM <project>.cdn_insights
WHERE timestamp >= now() - INTERVAL 24 HOUR
GROUP BY hdx_cdn
ORDER BY requests DESC
```

## Key Normalized Columns

These columns are present across all CDN sources and are the primary dimensions
for analysis:

| Column | Description |
|--------|-------------|
| `timestamp` | Request timestamp |
| `request_host` | Hostname requested |
| `request_path` | URL path |
| `request_method` | HTTP method |
| `response_status_code` | HTTP status code |
| `response_total_bytes` | Response size in bytes |
| `cache_was_cached` | Whether the response was served from cache |
| `client_country_iso_code` | Client country |
| `client_city` | Client city |
| `client_asn` | Client autonomous system number |
| `edge_pop` | Edge point of presence |
| `user_agent_category` | Classified user agent type |
| `hdx_cdn` | Which CDN provider served this request |
| `response_time_to_first_byte_ms` | Time to first byte (ms) |
| `response_time_to_last_byte_ms` | Time to last byte (ms) |
| `origin_time_to_first_byte_ms` | Origin TTFB (ms) |
| `origin_time_to_last_byte_ms` | Origin TTLB (ms) |
