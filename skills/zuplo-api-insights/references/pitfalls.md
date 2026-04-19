# zuplo-api-insights — Pitfalls

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
