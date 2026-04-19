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
- **Summary-first selection**: This bundle has minute/hour/day summaries. Prefer
  them when retained dimensions fit the question, especially for posture
  movement and executive trends.
- **Raw fallback is explicit**: Use request-level tables only when a required
  dimension is missing from summaries. State the fallback reason and keep tight
  time filters.
- **QoQ performance**: Do not assume quarter-over-quarter queries need monthly
  or quarterly summaries. Query or benchmark daily summaries first, then propose
  coarser summaries only with measured evidence.
- **Summary metadata**: Summary tables may expose aggregate-state columns in
  some deployments. Inspect table metadata with Hydrolix MCP or the host query
  tool before querying and use reported merge functions when required.
- **Local scripts**: Scripts must accept MCP query results or pasted aggregate
  JSON. Do not add database clients, connection settings, credential handling,
  or direct query execution.
- **Delta baselines**: The demo and dashboards use `(current - baseline) / greatest(baseline, 1) * 100`
  as the standard delta formula. Use the same approach for consistency when writing
  custom queries.
