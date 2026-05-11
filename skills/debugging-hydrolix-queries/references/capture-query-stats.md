# Capture Query Stats

Read this reference when you need exact examples for collecting Hydrolix query
statistics from HTTP, MCP, or native SQL clients.

After collecting `X-HDX-Query-Stats` or `hdx.active_queries.query_stats`, use
the bundled summarizer for a compact deterministic readout:

```bash
uv run python skills/debugging-hydrolix-queries/scripts/summarize_query_stats.py --file query-stats.json
```

## Contents

- [Choose the Channel](#choose-the-channel)
- [HTTP Query API](#http-query-api)
- [MCP or Native SQL Clients](#mcp-or-native-sql-clients)
- [Summarize Captured Stats](#summarize-captured-stats)

## Choose the Channel

`hdx_query_debug` is the most useful debugging flag. Over the HTTP Query API it
emits an `X-HDX-Query-Stats` response header with execution timing, bytes read,
partition count, peer count, memory usage, cache hit counts, and index usage.

MCP and native SQL clients may not expose HTTP response headers. In those
clients, tag the query with `hdx_query_comment` and inspect
`hdx.active_queries` within its 5-minute retention window.

## HTTP Query API

```sql
-- In the SQL itself.
SELECT count() FROM my_project.my_table
WHERE timestamp >= now() - INTERVAL 1 HOUR
SETTINGS
  hdx_query_debug = 1,
  hdx_query_comment = 'debug-<ticket-or-handle>';
```

```bash
# As query parameters.
curl -u USER:PASS \
  "https://HOST.hydrolix.live/query?hdx_query_debug=true&hdx_query_comment=debug-ticket-or-handle" \
  --data "SELECT count() FROM my_project.my_table WHERE ..."
```

```bash
# As an HTTP header, useful through proxies that rewrite URLs.
curl -u USER:PASS https://HOST.hydrolix.live/query \
  -H "X-HDX-query-settings: hdx_query_debug=true,hdx_query_comment=debug-ticket-or-handle" \
  --data "SELECT ..."
```

`X-HDX-Query-Stats` is returned only by the HTTP Query API. The TCP/native
ClickHouse protocol and most MCP tool responses do not surface it directly.

## MCP or Native SQL Client

Run the suspect query with a stable `hdx_query_comment`. If the client accepts
SQL `SETTINGS`, also set `hdx_query_debug = 1`.

```sql
SELECT count() FROM my_project.my_table
WHERE timestamp >= now() - INTERVAL 1 HOUR
SETTINGS
  hdx_query_debug = 1,
  hdx_query_comment = 'debug-<ticket-or-handle>';
```

Then immediately query `hdx.active_queries` by comment:

```sql
SELECT
  initial_query_id,
  query_start,
  query_end,
  active,
  mode,
  num_partitions,
  num_peers,
  formatReadableSize(memory_usage) AS current_mem,
  formatReadableSize(peak_memory_usage) AS peak_mem,
  query_stats,
  exception_code,
  exception_string,
  left(query, 160) AS query_preview
FROM hdx.active_queries
WHERE comment = 'debug-<ticket-or-handle>'
ORDER BY query_start DESC;
```

## Fields to Read

| Field | Tells you |
| --- | --- |
| `exec_time` | In-cluster execution time in ms; excludes client transfer. |
| `rows_read` | Rows scanned by query peers. |
| `bytes_read` | Uncompressed bytes parsed by query peers. |
| `num_partitions` | HDX partitions opened; high count usually means poor time pruning. |
| `num_peers` | Query peers that participated. |
| `memory_usage` | Memory used by the query head, in bytes. |
| `query_attempts` | Query retries; values above 1 can indicate peer or retryable failures. |
| `pool_name` | Query pool that served the request. |
| `limit_optimization` | Early-termination mode used, such as `order_by_limit_n`. |

Two nested objects are especially useful:

- `query_detail_runtime_stats`: splits bytes read into `cached_*` and `net_*`
  manifest, index, dictionary, and data reads. If `net_*` dominates, the query
  is missing cache. If `hdx_blocks_skipped` is high, the optimizer skipped work.
- `index_stats`: lists `columns_read`, `indexes_used`, and
  `shard_key_values_used`. Use it to confirm the planner is using the indexes
  and shard keys expected for JOIN and IN queries.
