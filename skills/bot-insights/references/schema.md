# bot_detection — Column Reference

Primary table schema for the **bot-insights** bundle.
Total columns: 85

| Indexed | Virtual | Suppressed |
|---------|---------|------------|
| 37 | 0 | 20 |

## Contents

- [Timestamp](#timestamp)
- [Request](#request)
- [Response](#response)
- [Cache](#cache)
- [Origin](#origin)
- [Client](#client)
- [Geo](#geo)
- [User Agent](#user-agent)
- [CDN](#cdn)
- [Security](#security)
- [Performance](#performance)
- [Identity](#identity)
- [Hydrolix](#hydrolix)
- [Other](#other)

## Timestamp

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `EdgeStartTimestamp` | string | suppressed | cloudflare |
| `firehose_timestamp` | epoch | suppressed | cloudfront_firehose |
| `origin_time_to_first_byte_ms` | uint64 | indexed | 8 transforms |
| `origin_time_to_first_byte_sec` | uint64 | indexed, suppressed | cloudfront_firehose |
| `origin_time_to_last_byte_ms` | uint64 | indexed | akamai_ds2 |
| `outcome_timestamp` | datetime |  | 8 transforms |
| `request_time_raw` | datetime | suppressed | tencent |
| `response_time_to_first_byte` | double | suppressed | fastly |
| `response_time_to_first_byte_sec` | double | suppressed | cloudfront_firehose |
| `time_to_first_byte_ms` | uint64 | indexed | 8 transforms |
| `timestamp` | epoch | primary | 8 transforms |

## Request

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `original_url` | string | indexed, suppressed | fastly |
| `protocol` | string |  | 8 transforms |
| `referer` | string | indexed, suppressed | akamai_ds2 |
| `request_headers` | string |  | 8 transforms |
| `request_headers_raw` | string | suppressed | akamai_siem, akamai_siem_gz |
| `request_host` | string | indexed | 8 transforms |
| `request_id` | string | suppressed | 7 transforms |
| `request_method` | string | indexed | 8 transforms |
| `request_path` | string | indexed | 8 transforms |
| `request_path_norm` | string | indexed | 8 transforms |
| `request_protocol` | string |  | 8 transforms |
| `request_query_string` | string | indexed, suppressed | akamai_ds2, cloudfront_firehose |
| `request_referer` | string |  | 8 transforms |
| `request_url` | string |  | 8 transforms |

## Response

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `content_type` | string | suppressed | akamai_ds2 |
| `origin_status_code` | uint32 |  | 8 transforms |
| `response_content_type` | string |  | 8 transforms |
| `response_headers` | string |  | 8 transforms |
| `response_status_code` | string | indexed | 8 transforms |
| `response_total_bytes` | uint64 | indexed | 8 transforms |

## Cache

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `cache_status` | string |  | 8 transforms |
| `cache_was_cached` | boolean | indexed | 8 transforms |

## Origin

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `origin_bytes` | uint64 |  | 8 transforms |
| `origin_ip` | string | indexed | 8 transforms |

## Client

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `client_asn` | string | indexed | 8 transforms |
| `client_city` | string | indexed | 8 transforms |
| `client_country_iso_code` | string | indexed | 8 transforms |
| `client_ip` | string | indexed | 8 transforms |
| `ja4_client_type` | string |  | 6 transforms |

## Geo

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `asn_type` | string |  | 6 transforms |
| `continent` | string |  | 8 transforms |
| `region_code` | string |  | 8 transforms |

## User Agent

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `bot_category` | string |  | 8 transforms |
| `bot_class` | string | indexed | 6 transforms |
| `bot_confidence` | string | indexed | 6 transforms |
| `bot_intent` | string | indexed | 6 transforms |
| `bot_producer` | string |  | 6 transforms |
| `bot_score` | uint8 |  | 8 transforms |
| `bot_type` | string |  | 8 transforms |
| `bot_verification_tier` | string | indexed | 6 transforms |
| `is_bot_traffic` | boolean | indexed | 8 transforms |
| `user_agent` | string | indexed | 8 transforms |
| `user_agent_category` | string | indexed | akamai_siem, akamai_siem_gz |
| `verified_bot_owner` | string |  | 6 transforms |

## CDN

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `edge_ip` | string | indexed | 8 transforms |
| `edge_pop` | string | indexed | 8 transforms |
| `edge_ttfb_ms` | uint64 |  | 8 transforms |
| `hdx_cdn` | string | indexed | 8 transforms |

## Security

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `attack_data` | string |  | 8 transforms |
| `attack_data_raw` | string | suppressed | akamai_siem, akamai_siem_gz |

## Performance

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `hdx_source_latency_sec` | uint32 | indexed | 8 transforms |

## Identity

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `session_id` | string |  | 8 transforms |

## Hydrolix

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `hdx_transform` | string | indexed | 8 transforms |

## Other

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `UA` | string | suppressed | akamai_ds2 |
| `action_taken` | string |  | 8 transforms |
| `action_taken_raw` | string | suppressed | akamai_siem, akamai_siem_gz |
| `ai_category` | string | indexed | 8 transforms |
| `auth_outcome` | string |  | 8 transforms |
| `breadcrumbs` | string | suppressed | akamai_ds2 |
| `business_outcome` | string |  | 8 transforms |
| `cacheStatus` | boolean | indexed, suppressed | akamai_ds2 |
| `config_id` | string |  | 8 transforms |
| `detailed_result_type` | string | indexed, suppressed | cloudfront_firehose |
| `enforcement_source` | string |  | 8 transforms |
| `ja3_hash` | string |  | 8 transforms |
| `ja4_hash` | string |  | 8 transforms |
| `policy_id` | string |  | 8 transforms |
| `port` | uint32 |  | 8 transforms |
| `queryStr` | string | suppressed | akamai_ds2 |
| `resource_category` | string | indexed | 8 transforms |
| `result_type` | string | indexed, suppressed | 4 transforms |
| `tls` | string |  | 8 transforms |
| `tor` | string |  | 8 transforms |
| `unknown` | map | indexed | 8 transforms |
