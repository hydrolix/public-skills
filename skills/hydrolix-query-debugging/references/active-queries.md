# Active Queries

Read this reference when a query is still running, just finished, or needs
query-ID, memory, partition, peer, exception, or SQL text inspection.

`hdx.active_queries` retains the last 5 minutes of query activity. It includes
full SQL text, user, comment, memory, partition and peer counts, exception
details, and a `query_stats` JSON string when stats are available.

## Find Long-Running Queries

```sql
SELECT
  initial_query_id,
  query_start,
  now() - query_start AS exec_secs,
  formatReadableSize(memory_usage) AS mem,
  formatReadableSize(peak_memory_usage) AS peak_mem,
  num_partitions,
  num_peers,
  query_stats,
  left(query, 120) AS query_preview
FROM hdx.active_queries
WHERE active = 1 AND mode = 'head'
ORDER BY exec_secs DESC;
```

## Inspect One Query End-to-End

```sql
SELECT
  initial_query_id,
  mode,
  query_start,
  query_end,
  num_peers,
  num_partitions,
  formatReadableSize(peak_memory_usage) AS peak_mem,
  JSONExtractInt(query_settings, 'query_timeout_secs') AS timeout_s,
  JSONExtractInt(query_settings, 'query_max_timerange_secs') AS max_range_s,
  query_stats,
  exception_code,
  exception_string,
  query
FROM hdx.active_queries
WHERE initial_query_id = '<uuid-from-X-Clickhouse-Query-Id-or-client-log>'
ORDER BY query_start;
```

Copy the UUID from the `X-Clickhouse-Query-Id` response header, from the
client's `query_id` log line, or by filtering on the `hdx_query_comment` value
set before running the query.

## Spot Memory Pressure

```sql
SELECT
  mode,
  formatReadableSize(sum(memory_usage)) AS total_mem,
  formatReadableSize(max(peak_memory_usage)) AS max_peak
FROM hdx.active_queries
WHERE active = 1
GROUP BY mode;
```

## Tag Diagnostic Queries

Dashboards, scripts, and automations should set `hdx_query_comment` so they
show up clearly in `hdx.active_queries.comment`. Grafana and Superset populate
`hdx_query_admin_comment` automatically with the submitting user.

```sql
SELECT ... SETTINGS hdx_query_comment = 'pagerduty-oom-triage';
```
