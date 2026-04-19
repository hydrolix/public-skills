# bot-insights — Pitfalls

## Pitfalls

- **Bot score range**: `bot_score` is a uint8 (0-255). A score of 0 does not
  necessarily mean human — check `is_bot_traffic` for the boolean classification.
- **CDN-specific enrichment**: `bot_class`, `bot_confidence`, `bot_intent`, and
  `bot_verification_tier` come from 6 transforms (primarily Akamai SIEM). They
  may be empty for traffic from other CDN sources.
- **Akamai vs. Hydrolix columns**: Akamai-provided signals (`bot_score`,
  `bot_category`, `bot_type`) and Hydrolix-derived signals (`bot_class`,
  `bot_intent`, `bot_confidence`) are independent. Divergences between the two
  are investigative signals, not errors.
- **`user_agent_category`**: Only populated from Akamai SIEM transforms, not
  all 8 CDN sources. Use `bot_category` for broader coverage.
- **`response_status_code` is a string**: Use string comparison (e.g.,
  `>= '400'`) or cast with `toUInt32OrZero()`.
- **SIEM/DS2 deduplication**: The same request can appear in both SIEM and DS2
  feeds. Be explicit about which data source you are querying when counting.
  Filter by `hdx_cdn` to isolate a single source.
- **Suppressed columns**: `attack_data_raw`, `request_headers_raw`,
  `request_query_string`, and several others are suppressed. Use the normalized
  equivalents.
- **High volume**: This table can be very large. Always apply time filters.
  There are no summary tables in this bundle, so narrow your time windows for
  aggregations.
- **Delta baselines**: The demo and dashboards use `(current - baseline) / greatest(baseline, 1) * 100`
  as the standard delta formula. Use the same approach for consistency when writing
  custom queries.
