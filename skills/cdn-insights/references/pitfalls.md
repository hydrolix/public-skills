# cdn-insights — Pitfalls

## Pitfalls

- **CDN-specific columns**: Many columns only exist for one CDN source (prefixed
  with `akamai_`, `cloudflare_`, etc.). Filter by `hdx_cdn` when using these.
- **Suppressed columns**: Columns marked `suppressed` in the schema are stored but
  excluded from default queries. They are typically raw/unnormalized variants of
  normalized fields. Use the normalized versions instead.
- **Large time ranges**: Always use summary tables for queries spanning more than a
  few hours. The primary table can have billions of rows.
- **response_total_bytes**: This is the normalized bytes field across all CDNs. Use
  this instead of CDN-specific byte fields.
