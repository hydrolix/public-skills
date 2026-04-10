# cdn_insights — Column Reference

Primary table schema for the **cdn-insights** bundle.
Total columns: 316

| Indexed | Virtual | Suppressed |
|---------|---------|------------|
| 212 | 7 | 62 |

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
| `cloudflare_edge_end_timestamp` | string | indexed | cloudflare |
| `cloudflare_origin_dns_response_time_ms` | uint32 | indexed | cloudflare |
| `cloudflare_worker_cpu_time` | uint32 | indexed | cloudflare |
| `cloudflare_worker_wall_time_us` | uint32 | indexed | cloudflare |
| `ds_req_time` | string | suppressed | byteplus |
| `firehose_timestamp` | epoch | suppressed | cloudfront_firehose |
| `origin_time_to_first_byte_ms` | uint64 | indexed | 12 transforms |
| `origin_time_to_first_byte_sec` | uint64 | indexed, suppressed | cloudfront_firehose |
| `origin_time_to_last_byte_ms` | uint64 | indexed | 12 transforms |
| `origin_time_to_last_byte_sec` | uint64 | indexed, suppressed | cloudfront_firehose |
| `response_time` | double | suppressed | cachefly |
| `response_time_to_first_byte` | double | suppressed | fastly |
| `response_time_to_first_byte_ms` | uint64 | indexed | 12 transforms |
| `response_time_to_first_byte_sec` | double | suppressed | cloudfront_firehose |
| `response_time_to_last_byte` | double | suppressed | fastly |
| `response_time_to_last_byte_ms` | uint64 | indexed | 12 transforms |
| `response_time_to_last_byte_sec` | double | suppressed | cloudfront_firehose |
| `tencent_edge_end_time` | string |  | tencent |
| `tencent_log_time` | string |  | tencent |
| `timestamp` | datetime / epoch | primary | 12 transforms |
| `varnish_time_firstbyte_sec` | double | suppressed | varnish |
| `varnish_time_to_last_byte_ms` | uint64 | suppressed | varnish |

## Request

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `akamai_req_id` | string | indexed | akamai_ds2 |
| `akamai_request_port` | string | indexed | akamai_ds2 |
| `byteplus_ds_http_protocol` | string | indexed | byteplus |
| `cloudflare_client_request_query` | string | indexed | cloudflare |
| `cloudflare_edge_pathing_op` | string | indexed | cloudflare |
| `cloudflare_edge_pathing_src` | string | indexed | cloudflare |
| `cloudflare_edge_pathing_status` | string | indexed | cloudflare |
| `cloudflare_edge_request_host` | string | indexed | cloudflare |
| `cloudflare_origin_ssl_protocol` | string | indexed | cloudflare |
| `cloudflare_request_bytes` | uint64 | indexed | cloudflare |
| `cloudflare_request_protocol` | string | indexed | cloudflare |
| `cloudflare_request_scheme` | string | indexed | cloudflare |
| `cloudflare_request_source` | string | indexed | cloudflare |
| `cloudflare_ssl_protocol` | string | indexed | cloudflare |
| `cloudflare_worker_subrequest_count` | uint32 | indexed | cloudflare |
| `cloudfront_cs_protocol` | string | indexed | cloudfront_firehose |
| `cloudfront_cs_protocol_version` | string | indexed | cloudfront_firehose |
| `cloudfront_edge_request_id` | string | indexed | cloudfront_firehose |
| `cloudfront_request_bytes` | uint64 | indexed | cloudfront_firehose |
| `cloudfront_ssl_protocol` | string | indexed | cloudfront_firehose |
| `gmcdn_http_protocol` | string | indexed, suppressed | google_media |
| `gmcdn_matched_path` | string | indexed, suppressed | google_media |
| `gmcdn_original_request_id` | string | suppressed | google_media |
| `gmcdn_request_id` | string | suppressed | google_media |
| `gmcdn_request_size` | uint64 | suppressed | google_media |
| `hdx_method` | string | indexed | byteplus |
| `original_url` | string | indexed, suppressed | fastly |
| `referer` | string | indexed | akamai_ds2 |
| `request_full_path` | string | indexed, suppressed | 5 transforms |
| `request_host` | string | indexed | 12 transforms |
| `request_id` | string | suppressed | cloudfront_firehose |
| `request_line` | string | suppressed | cachefly |
| `request_method` | string | indexed | 12 transforms |
| `request_path` | string | indexed | 12 transforms |
| `request_query_string` | string | indexed | 12 transforms |
| `request_referer` | string | indexed | 12 transforms |
| `tencent_origin_ssl_protocol` | string |  | tencent |
| `tencent_parent_request_id` | string |  | tencent |
| `tencent_request_body_bytes` | uint64 |  | tencent |
| `tencent_request_bytes` | uint64 |  | tencent |
| `tencent_request_id` | string |  | tencent |
| `tencent_request_protocol` | string |  | tencent |
| `tencent_request_range` | string |  | tencent |
| `tencent_request_scheme` | string |  | tencent |
| `tencent_request_ssl_protocol` | string |  | tencent |
| `tencent_request_status` | string |  | tencent |

## Response

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `akamai_response_content_length` | uint64 | indexed | akamai_ds2 |
| `byteplus_ds_http_resp_content_length` | uint64 |  | byteplus |
| `cloudflare_cache_response_bytes` | uint64 | indexed | cloudflare |
| `cloudflare_origin_response_http_expires` | string | indexed | cloudflare |
| `cloudflare_origin_response_http_last_modified` | string | indexed | cloudflare |
| `cloudflare_origin_response_status` | uint32 | indexed | cloudflare |
| `cloudflare_response_body_bytes` | uint64 | indexed | cloudflare |
| `cloudflare_response_compression_ratio` | double |  | cloudflare |
| `cloudfront_edge_response_result_type` | string | indexed | cloudfront_firehose |
| `cloudfront_response_content_length` | uint64 | indexed | cloudfront_firehose |
| `response_content_type` | string | indexed | 12 transforms |
| `response_status_code` | string | indexed | 12 transforms |
| `response_total_bytes` | uint64 | indexed | 12 transforms |
| `tencent_edge_response_body_bytes` | uint64 |  | tencent |
| `tencent_origin_status_code` | string |  | tencent |

## Cache

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `cache_outcome_category` | string | indexed | akamai_ds2 |
| `cache_was_cached` | boolean | indexed, virtual | 12 transforms |
| `cloudflare_cache_reserve_used` | boolean | indexed | cloudflare |
| `cloudflare_cache_tiered_fill` | boolean | indexed | cloudflare |
| `gmcdn_cache_id` | string | suppressed | google_media |
| `gmcdn_cache_key_fingerprint` | string | suppressed | google_media |
| `gmcdn_cache_mode` | string | indexed, suppressed | google_media |
| `gmcdn_cache_status_raw` | string | suppressed | google_media |
| `gmcdn_client_cache_status` | string | indexed, suppressed | google_media |
| `is_cached` | string | indexed, suppressed | ioriver |

## Origin

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `cloudflare_origin_tcp_handshake_duration_ms` | uint32 | indexed | cloudflare |
| `cloudflare_origin_tls_handshake_duration_ms` | uint32 | indexed | cloudflare |
| `gmcdn_origin_name` | string | indexed, suppressed | google_media |
| `is_origin_request` | boolean | indexed | 12 transforms |
| `origin_ip` | string | indexed | 12 transforms |
| `tencent_origin_dns_duration` | double |  | tencent |
| `tencent_origin_header_send_duration` | double |  | tencent |
| `tencent_origin_tcp_handshake_duration` | double |  | tencent |
| `tencent_origin_tls_handshake_duration` | double |  | tencent |

## Client

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `akamai_client_state` | string | indexed | akamai_ds2 |
| `byteplus_client_ip_country` | string | indexed | byteplus |
| `byteplus_client_ip_isp` | string | indexed | byteplus |
| `byteplus_client_ip_province` | string | indexed | byteplus |
| `byteplus_client_port` | string |  | byteplus |
| `client_asn` | string | indexed | 12 transforms |
| `client_city` | string | indexed, virtual | 12 transforms |
| `client_country_iso_code` | string | indexed | 12 transforms |
| `client_ip` | string | indexed | 12 transforms |
| `cloudflare_client_device_type` | string | indexed | cloudflare |
| `cloudflare_client_ip_class` | string | indexed | cloudflare |
| `cloudflare_client_latitude` | string | indexed | cloudflare |
| `cloudflare_client_longitude` | string | indexed | cloudflare |
| `cloudflare_client_region_code` | string | indexed | cloudflare |
| `cloudflare_client_src_port` | uint32 | indexed | cloudflare |
| `cloudflare_client_state` | string | indexed | cloudflare |
| `cloudflare_client_tcp_rtt_ms` | uint32 | indexed | cloudflare |
| `cloudfront_client_port` | string | indexed | cloudfront_firehose |
| `imperva_client_app` | string | indexed | imperva |
| `imperva_client_port` | string |  | imperva |
| `imperva_client_signature` | string |  | imperva |
| `tencent_client_connection_id` | string |  | tencent |
| `tencent_client_device_type` | string |  | tencent |
| `tencent_client_port` | uint64 |  | tencent |
| `tencent_client_state` | string |  | tencent |
| `tencent_remote_port` | uint64 |  | tencent |
| `varnish_client_asn` | string | suppressed | varnish |
| `varnish_client_city` | string | suppressed | varnish |
| `varnish_client_country_iso_code` | string | suppressed | varnish |

## Geo

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `akamai_billing_region` | string | indexed | akamai_ds2 |
| `akamai_server_country` | string | indexed | akamai_ds2 |
| `gmcdn_flex_shielding_region` | string | suppressed | google_media |
| `gmcdn_proxy_region_code` | string | indexed, suppressed | google_media |
| `tencent_edge_server_region` | string |  | tencent |

## User Agent

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `cloudflare_bot_detection_ids` | array |  | cloudflare |
| `cloudflare_bot_detection_tags` | array |  | cloudflare |
| `cloudflare_bot_score` | uint32 | indexed | cloudflare |
| `cloudflare_bot_score_src` | string | indexed | cloudflare |
| `cloudflare_bot_tags` | array |  | cloudflare |
| `is_bot_traffic` | boolean | indexed, virtual | 10 transforms |
| `tencent_bot_characteristic` | string |  | tencent |
| `tencent_bot_class_account_takeover` | string |  | tencent |
| `tencent_bot_class_attacker` | string |  | tencent |
| `tencent_bot_class_malicious_bot` | string |  | tencent |
| `tencent_bot_class_proxy` | string |  | tencent |
| `tencent_bot_class_scanner` | string |  | tencent |
| `tencent_bot_tag` | string |  | tencent |
| `user_agent` | string | indexed | 12 transforms |
| `user_agent_category` | string | indexed, virtual | 12 transforms |

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
| `cloudflare_edge_cf_connecting_o2o` | boolean | indexed | cloudflare |
| `cloudflare_edge_colo_id` | uint32 | indexed | cloudflare |
| `cloudflare_parent_ray_id` | string | indexed | cloudflare |
| `cloudflare_ray_id` | string | indexed | cloudflare |
| `cloudflare_security_action` | string | indexed | cloudflare |
| `cloudflare_security_actions` | array |  | cloudflare |
| `cloudflare_security_rule_description` | string | indexed | cloudflare |
| `cloudflare_security_rule_id` | string | indexed | cloudflare |
| `cloudflare_security_rule_ids` | array |  | cloudflare |
| `cloudflare_security_sources` | array |  | cloudflare |
| `cloudflare_smart_route_colo_id` | uint32 | indexed | cloudflare |
| `cloudflare_ssl_cipher` | string | indexed | cloudflare |
| `cloudflare_upper_tier_colo_id` | uint32 | indexed | cloudflare |
| `cloudflare_waf_attack_score` | uint32 | indexed | cloudflare |
| `cloudflare_waf_rce_attack_score` | uint32 | indexed | cloudflare |
| `cloudflare_waf_sqli_attack_score` | uint32 | indexed | cloudflare |
| `cloudflare_waf_xss_attack_score` | uint32 | indexed | cloudflare |
| `cloudflare_worker_script_name` | string | indexed | cloudflare |
| `cloudflare_worker_status` | string | indexed | cloudflare |
| `cloudflare_worker_subrequest` | boolean | indexed | cloudflare |
| `cloudflare_x_requested_with` | string | indexed | cloudflare |
| `cloudflare_zone_name` | string | indexed | cloudflare |
| `cloudfront_cookie` | string |  | cloudfront_firehose |
| `cloudfront_cs_host` | string | indexed | cloudfront_firehose |
| `cloudfront_fle_encrypted_fields` | string | indexed | cloudfront_firehose |
| `cloudfront_fle_status` | string | indexed | cloudfront_firehose |
| `cloudfront_range_end` | uint64 | indexed | cloudfront_firehose |
| `cloudfront_range_start` | uint64 | indexed | cloudfront_firehose |
| `cloudfront_ssl_cipher` | string | indexed | cloudfront_firehose |
| `cloudfront_x_forwarded_for` | string | indexed | cloudfront_firehose |
| `edge_ip` | string | indexed | 12 transforms |
| `edge_pop` | string | indexed | 12 transforms |
| `fastly_is_edge` | boolean | indexed | fastly |
| `gmcdn_compression` | string | suppressed | google_media |
| `gmcdn_flex_shielding_status` | string | indexed, suppressed | google_media |
| `gmcdn_insert_id` | string | suppressed | google_media |
| `gmcdn_project_id` | string | indexed, suppressed | google_media |
| `gmcdn_proxy_status` | string | suppressed | google_media |
| `gmcdn_range_header` | string | suppressed | google_media |
| `gmcdn_route_type` | string | indexed, suppressed | google_media |
| `gmcdn_service_name` | string | indexed, suppressed | google_media |
| `gmcdn_tls_cipher_suite` | string | suppressed | google_media |
| `gmcdn_tls_sni_hostname` | string | suppressed | google_media |
| `gmcdn_tls_version` | string | indexed, suppressed | google_media |
| `hdx_cdn` | string | indexed | 12 transforms |
| `tencent_challenge_state` | string |  | tencent |
| `tencent_edge_exception` | string |  | tencent |
| `tencent_edge_function_subrequest` | uint32 |  | tencent |
| `tencent_ja3_hash` | string |  | tencent |
| `tencent_security_action` | string |  | tencent |
| `tencent_security_module` | string |  | tencent |
| `tencent_security_rule_id` | string |  | tencent |

## Security

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `imperva_attack_id` | string |  | imperva |
| `imperva_attack_type` | string |  | imperva |

## Performance

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `OriginResponseHeaderDuration` | double | suppressed | tencent |
| `hdx_source_latency_sec` | uint32 | indexed, virtual | 12 transforms |

## Identity

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `imperva_account_id` | string | indexed | imperva |

## Hydrolix

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `hdx_transform` | string | indexed | 12 transforms |

## Other

| Column | Type | Flags | Sources |
|--------|------|-------|---------|
| `UA` | string | indexed, suppressed | akamai_ds2 |
| `ai_category` | string | indexed, virtual | 8 transforms |
| `breadcrumbs` | string | suppressed | akamai_ds2 |
| `byteplus_ds_http_range` | string |  | byteplus |
| `byteplus_ds_http_scheme` | string | indexed | byteplus |
| `byteplus_ds_http_uri` | string | indexed, suppressed | byteplus |
| `cacheStatus` | boolean | indexed, suppressed | akamai_ds2 |
| `cachefly_connection_type` | string | indexed | cachefly |
| `cachefly_isp` | string | indexed | cachefly |
| `cachefly_service_uid` | string | indexed | cachefly |
| `contentProtectionInfo` | string | indexed, suppressed | akamai_ds2 |
| `detailed_result_type` | string | indexed, suppressed | cloudfront_firehose |
| `earlyHints` | string | indexed, suppressed | akamai_ds2 |
| `ewExecutionInfo` | string | indexed, suppressed | akamai_ds2 |
| `ewUsageInfo` | string | indexed, suppressed | akamai_ds2 |
| `imperva_captcha_support` | string |  | imperva |
| `imperva_cookie_support` | string |  | imperva |
| `imperva_event_name` | string | indexed | imperva |
| `imperva_js_support` | string |  | imperva |
| `imperva_severity` | string | indexed | imperva |
| `imperva_tls_version` | string |  | imperva |
| `imperva_visitor_id` | string |  | imperva |
| `imperva_xff` | string |  | imperva |
| `ioriver_content_encoding` | string | indexed | ioriver |
| `ioriver_cookie` | string |  | ioriver |
| `ioriver_http_version` | string | indexed | ioriver |
| `ioriver_midgress_bytes` | uint64 |  | ioriver |
| `ioriver_provider` | string | indexed | ioriver |
| `ioriver_service_id` | string |  | ioriver |
| `ioriver_service_uid` | string | indexed | ioriver |
| `ioriver_status_phrase` | string |  | ioriver |
| `ioriver_unified_logs_behavior_id` | string | indexed | ioriver |
| `ioriver_x_forwarded_for` | string |  | ioriver |
| `is_edge` | boolean | suppressed | fastly |
| `pop` | string | suppressed | cachefly |
| `queryStr` | string | indexed, suppressed | akamai_ds2 |
| `request` | string | suppressed | imperva |
| `resource_category` | string | indexed, virtual | 8 transforms |
| `result_type` | string | indexed, suppressed | 10 transforms |
| `securityRules` | string | indexed, suppressed | akamai_ds2 |
| `sid` | string | indexed | akamai_ds2 |
| `unknown` | map | indexed | 12 transforms |
| `varnish_handling` | string | indexed | varnish |
| `varnish_parent_id` | string |  | varnish |
| `varnish_tracing` | string | indexed | varnish |
| `varnish_vxid` | string |  | varnish |
