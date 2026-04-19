# zuplo_gateway — Column Reference

Primary table schema for the **zuplo-api-insights** bundle.
Total columns: 159

| Indexed | Virtual | Suppressed |
|---------|---------|------------|
| 121 | 0 | 38 |

## Timestamp

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `akamai_dns_lookup_time_ms` | uint64 | indexed | akamai_ds2 |
| `akamai_download_time_ms` | uint64 | indexed | akamai_ds2 |
| `akamai_ew_process_time` | string | indexed | akamai_ds2 |
| `akamai_ew_total_stage_time` | string | indexed | akamai_ds2 |
| `akamai_ew_total_time` | string | indexed | akamai_ds2 |
| `akamai_request_end_time_ms` | uint64 | indexed | akamai_ds2 |
| `akamai_tls_overhead_time_ms` | uint64 | indexed | akamai_ds2 |
| `akamai_turnaround_time_ms` | uint64 | indexed | akamai_ds2 |
| `origin_time_to_first_byte_ms` | uint64 | indexed | akamai_ds2 |
| `origin_time_to_last_byte_ms` | uint64 | indexed | akamai_ds2 |
| `response_time_to_first_byte_ms` | uint64 | indexed | akamai_ds2 |
| `response_time_to_last_byte_ms` | uint64 | indexed | akamai_ds2 |
| `timestamp` | datetime | primary | akamai_ds2, zuplo_gateway |
| `timestamp_raw` | datetime | suppressed | zuplo_gateway |
| `timezone_raw` | string | suppressed | zuplo_gateway |
| `zuplo_timezone` | string | indexed | zuplo_gateway |

## Request

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `akamai_req_id` | string | indexed | akamai_ds2 |
| `akamai_request_port` | string | indexed | akamai_ds2 |
| `method_raw` | string | suppressed | zuplo_gateway |
| `referer` | string | indexed | akamai_ds2 |
| `request_host` | string | indexed | akamai_ds2 |
| `request_id` | string | indexed | zuplo_gateway |
| `request_id_raw` | string | suppressed | zuplo_gateway |
| `request_method` | string | indexed | akamai_ds2, zuplo_gateway |
| `request_path` | string | indexed | akamai_ds2, zuplo_gateway |
| `request_query` | string | indexed | zuplo_gateway |
| `request_query_string` | string | indexed | akamai_ds2 |
| `request_referer` | string | indexed | akamai_ds2 |
| `request_url` | string |  | zuplo_gateway |
| `request_url_raw` | string | suppressed | zuplo_gateway |
| `route_path` | string | indexed | zuplo_gateway |
| `route_path_raw` | string | suppressed | zuplo_gateway |

## Response

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `akamai_response_content_length` | uint64 | indexed | akamai_ds2 |
| `response_content_type` | string | indexed | akamai_ds2 |
| `response_status_code` | string | indexed | akamai_ds2, zuplo_gateway |
| `response_total_bytes` | uint64 | indexed | akamai_ds2 |
| `status_code_raw` | string | suppressed | zuplo_gateway |

## Cache

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `cache_outcome_category` | string | indexed | akamai_ds2 |
| `cache_was_cached` | boolean | indexed | akamai_ds2 |

## Origin

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `is_origin_request` | boolean | indexed | akamai_ds2 |
| `origin_ip` | string | indexed | akamai_ds2 |

## Client

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `akamai_client_state` | string | indexed | akamai_ds2 |
| `client_asn` | string | indexed | akamai_ds2, zuplo_gateway |
| `client_city` | string | indexed | akamai_ds2 |
| `client_country` | string | indexed | zuplo_gateway |
| `client_country_iso_code` | string | indexed | akamai_ds2 |
| `client_ip` | string | indexed | akamai_ds2, zuplo_gateway |
| `client_ip_raw` | string | suppressed | zuplo_gateway |

## Geo

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `akamai_billing_region` | string | indexed | akamai_ds2 |
| `akamai_server_country` | string | indexed | akamai_ds2 |
| `asn_raw` | string | suppressed | zuplo_gateway |
| `city_raw` | string | suppressed | zuplo_gateway |
| `continent_raw` | string | suppressed | zuplo_gateway |
| `country_raw` | string | suppressed | zuplo_gateway |
| `latitude_raw` | string | suppressed | zuplo_gateway |
| `longitude_raw` | string | suppressed | zuplo_gateway |
| `region_code_raw` | string | suppressed | zuplo_gateway |
| `region_raw` | string | suppressed | zuplo_gateway |
| `zuplo_city` | string | indexed | zuplo_gateway |
| `zuplo_continent` | string | indexed | zuplo_gateway |
| `zuplo_latitude` | string |  | zuplo_gateway |
| `zuplo_longitude` | string |  | zuplo_gateway |
| `zuplo_region` | string | indexed | zuplo_gateway |
| `zuplo_region_code` | string | indexed | zuplo_gateway |

## User Agent

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `is_bot_traffic` | boolean | indexed | akamai_ds2 |
| `user_agent` | string | indexed | akamai_ds2, zuplo_gateway |
| `user_agent_category` | string | indexed | akamai_ds2 |
| `user_agent_raw` | string | suppressed | zuplo_gateway |

## CDN

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `akamai_accept_language` | string | indexed | akamai_ds2 |
| `akamai_bytes` | uint64 | indexed | akamai_ds2 |
| `akamai_cacheable` | boolean | indexed | akamai_ds2 |
| `akamai_cookie` | string |  | akamai_ds2 |
| `akamai_cp` | string | indexed | akamai_ds2 |
| `akamai_custom_field` | string | indexed | akamai_ds2 |
| `akamai_early_hints` | string | suppressed | akamai_ds2 |
| `akamai_early_hints_akamai_hints` | uint32 | indexed | akamai_ds2 |
| `akamai_early_hints_akamai_hints_bytes` | uint32 | indexed | akamai_ds2 |
| `akamai_early_hints_user_hints` | uint32 | indexed | akamai_ds2 |
| `akamai_early_hints_user_hints_bytes` | uint32 | indexed | akamai_ds2 |
| `akamai_edge_ip` | string | indexed | akamai_ds2 |
| `akamai_epd_action_code` | string | indexed, suppressed | akamai_ds2 |
| `akamai_epd_action_name` | string | indexed | akamai_ds2 |
| `akamai_epd_category` | string | indexed | akamai_ds2 |
| `akamai_epd_code` | string | indexed, suppressed | akamai_ds2 |
| `akamai_epd_match` | boolean | indexed | akamai_ds2 |
| `akamai_error_code` | string | indexed | akamai_ds2 |
| `akamai_ew_cpu_flits` | string | indexed | akamai_ds2 |
| `akamai_ew_edge_server_flow` | string | indexed | akamai_ds2 |
| `akamai_ew_error_code` | string | indexed | akamai_ds2 |
| `akamai_ew_event_handler` | string | indexed | akamai_ds2 |
| `akamai_ew_execution_info` | string |  | akamai_ds2 |
| `akamai_ew_http_status` | string | indexed | akamai_ds2 |
| `akamai_ew_id` | string | indexed | akamai_ds2 |
| `akamai_ew_logic_executed` | string | indexed | akamai_ds2 |
| `akamai_ew_off_reason` | string | indexed | akamai_ds2 |
| `akamai_ew_revision_id` | string | indexed | akamai_ds2 |
| `akamai_ew_stage_information` | string | indexed | akamai_ds2 |
| `akamai_ew_status` | string | indexed | akamai_ds2 |
| `akamai_ew_tier_id` | string | indexed | akamai_ds2 |
| `akamai_ew_uid` | string | indexed | akamai_ds2 |
| `akamai_ew_usage_info` | string |  | akamai_ds2 |
| `akamai_ew_used_memory` | string | indexed | akamai_ds2 |
| `akamai_ew_version` | string | indexed | akamai_ds2 |
| `akamai_last_byte` | string | indexed | akamai_ds2 |
| `akamai_max_age_sec` | uint32 | indexed | akamai_ds2 |
| `akamai_object_size` | uint64 | indexed | akamai_ds2 |
| `akamai_overhead_bytes` | uint64 | indexed | akamai_ds2 |
| `akamai_proto` | string | indexed | akamai_ds2 |
| `akamai_range` | string | indexed | akamai_ds2 |
| `akamai_security_denied` | boolean | indexed | akamai_ds2 |
| `akamai_security_deny_group` | string | indexed | akamai_ds2 |
| `akamai_security_deny_rule` | string | indexed | akamai_ds2 |
| `akamai_security_policy` | string | indexed | akamai_ds2 |
| `akamai_security_rules` | string | indexed | akamai_ds2 |
| `akamai_security_rules_triggered` | array |  | akamai_ds2 |
| `akamai_stream_id` | string | indexed | akamai_ds2 |
| `akamai_throughput` | uint64 | indexed | akamai_ds2 |
| `akamai_tls_version` | string | indexed | akamai_ds2 |
| `akamai_uncompressed_size` | uint64 | indexed | akamai_ds2 |
| `akamai_version` | uint32 | indexed | akamai_ds2 |
| `akamai_x_forwarded_for` | string | indexed | akamai_ds2 |
| `edge_ip` | string | indexed | akamai_ds2 |
| `edge_pop` | string | indexed | akamai_ds2 |
| `hdx_cdn` | string | indexed | akamai_ds2 |

## Performance

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `duration_ms_raw` | uint32 | suppressed | zuplo_gateway |
| `gateway_latency_ms` | uint32 | indexed | zuplo_gateway |
| `hdx_source_latency_sec` | uint32 | indexed | akamai_ds2 |

## Hydrolix

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `hdx_transform` | string | indexed | akamai_ds2 |

## Other

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `UA` | string | indexed, suppressed | akamai_ds2 |
| `as_organization_raw` | string | suppressed | zuplo_gateway |
| `auth_outcome` | string | indexed | zuplo_gateway |
| `breadcrumbs` | string | suppressed | akamai_ds2 |
| `cacheStatus` | boolean | indexed, suppressed | akamai_ds2 |
| `colo_raw` | string | suppressed | zuplo_gateway |
| `consumer_id` | string | indexed | zuplo_gateway |
| `contentProtectionInfo` | string | indexed, suppressed | akamai_ds2 |
| `deployment_name` | string | indexed | zuplo_gateway |
| `deployment_name_raw` | string | suppressed | zuplo_gateway |
| `earlyHints` | string | indexed, suppressed | akamai_ds2 |
| `ewExecutionInfo` | string | indexed, suppressed | akamai_ds2 |
| `ewUsageInfo` | string | indexed, suppressed | akamai_ds2 |
| `gateway_outcome` | string | indexed | zuplo_gateway |
| `instance_id_raw` | string | suppressed | zuplo_gateway |
| `metro_code_raw` | string | suppressed | zuplo_gateway |
| `operation_id` | string | indexed | zuplo_gateway |
| `operation_id_raw` | string | suppressed | zuplo_gateway |
| `postal_code_raw` | string | suppressed | zuplo_gateway |
| `queryStr` | string | indexed, suppressed | akamai_ds2 |
| `rate_limit_outcome` | string | indexed | zuplo_gateway |
| `route_group` | string | indexed | zuplo_gateway |
| `securityRules` | string | indexed, suppressed | akamai_ds2 |
| `sid` | string | indexed | akamai_ds2 |
| `unknown` | map | indexed | akamai_ds2, zuplo_gateway |
| `user_sub_raw` | string | suppressed | zuplo_gateway |
| `zuplo_as_organization` | string | indexed | zuplo_gateway |
| `zuplo_colo` | string | indexed | zuplo_gateway |
| `zuplo_instance_id` | string | indexed | zuplo_gateway |
| `zuplo_metro_code` | string |  | zuplo_gateway |
| `zuplo_postal_code` | string |  | zuplo_gateway |
