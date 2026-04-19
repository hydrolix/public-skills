---
name: hydrolix-query-debugging
description: >
  Debug slow, failing, or resource-hungry SQL queries on a Hydrolix cluster.
  Use when a query is timing out, out-of-memory, returning a DB::Exception or
  HdxStorageError, hitting a circuit breaker, being canceled by the query head,
  or simply running slower than expected over MCP, the HTTP Query API, or
  another SQL client.
license: Apache-2.0
metadata:
  version: 1.0.0
  author: Hydrolix
---

# Hydrolix Query Debugging

Use this skill to diagnose a misbehaving Hydrolix SQL query without loading
every troubleshooting detail up front. Start with this file, classify the
problem, then read only the reference file that matches the client path or
failure mode.

## Progressive Disclosure Rule

Do not read all bundled references at startup. Load the smallest relevant
reference when one of these conditions is true:

- You need exact SQL / curl examples for collecting query stats.
- You need `hdx.active_queries` inspection queries.
- You have a concrete error fragment to classify.
- You are about to recommend a circuit-breaker or persistent setting change.
- You need rewrite guidance for a slow query that is not failing.

Prefer the bundled scripts when their input is already available and a
deterministic parse is useful. Do not use scripts to connect to a cluster,
change settings, or infer persistent Config API changes.

## When to Use

Use this skill when the user reports or you observe:

- Timeout, out-of-memory, result-size, max-columns, max-rows, max-partitions,
  or max-timerange errors.
- `HdxStorageError hdx_query_timerange_required is set to true`.
- `HdxStorageError No peers available to run query in pool`.
- `DB::NetException: Timeout: connect timed out` or `No route to host`.
- A query, dashboard panel, MCP query, or SQL client request that used to be
  fast and is now slow.
- A need to determine which partitions, peers, bytes, memory, cache, or indexes
  a query actually used.

## Decision Flow

1. If the query already failed, read only the first 1-2 error lines and open
   [references/error-taxonomy.md](references/error-taxonomy.md). Optionally run
   `scripts/classify_error.py` on the error fragment first.
2. If the query is still running or just finished and you need query IDs,
   memory, partition counts, peers, comments, or exceptions, open
   [references/active-queries.md](references/active-queries.md).
3. If you need query stats from the HTTP Query API, MCP, or a native SQL
   client, open [references/capture-query-stats.md](references/capture-query-stats.md).
   Optionally run `scripts/summarize_query_stats.py` on the stats JSON.
4. If the next step would raise or recommend a circuit breaker, spill-to-disk,
   pool, concurrency, or persistent Config API setting, open
   [references/circuit-breakers.md](references/circuit-breakers.md).
5. If the query is slow but not clearly blocked by a specific error, open
   [references/query-rewrite-patterns.md](references/query-rewrite-patterns.md).

## Minimal Triage Loop

1. Preserve the original query and the exact client path: HTTP Query API, MCP,
   native ClickHouse/MySQL client, dashboard, or another SQL client.
2. For failed queries, classify the error before changing settings.
3. For slow queries, tag one diagnostic run with `hdx_query_comment`; use
   `hdx_query_debug = 1` when the client supports query settings.
4. Inspect query stats or `hdx.active_queries` within the 5-minute retention
   window.
5. Prefer query rewrites and narrower filters before raising limits.
6. Apply per-query `SETTINGS` first. Do not recommend persistent org, project,
   or table changes unless the user or cluster operator explicitly asks for
   that scope and you have a rollback value.

## Fast Commands to Remember

These snippets are intentionally minimal. Open the matching reference before
using them in a real debugging session.

```sql
-- Tag a diagnostic run.
SELECT ...
SETTINGS
  hdx_query_debug = 1,
  hdx_query_comment = 'debug-<ticket-or-handle>';
```

```sql
-- Find currently active query heads.
SELECT
  initial_query_id,
  query_start,
  now() - query_start AS exec_secs,
  num_partitions,
  num_peers,
  formatReadableSize(peak_memory_usage) AS peak_mem,
  left(query, 120) AS query_preview
FROM hdx.active_queries
WHERE active = 1 AND mode = 'head'
ORDER BY exec_secs DESC;
```

## Reference Map

- [scripts/classify_error.py](scripts/classify_error.py): classify a pasted
  Hydrolix error fragment into a likely root cause and first action.
- [scripts/summarize_query_stats.py](scripts/summarize_query_stats.py):
  summarize `X-HDX-Query-Stats` or `hdx.active_queries.query_stats` JSON.
- [references/capture-query-stats.md](references/capture-query-stats.md):
  HTTP `X-HDX-Query-Stats`, MCP/native comments, and stats fields.
- [references/active-queries.md](references/active-queries.md):
  `hdx.active_queries` retention, lookup, memory, exception, and query-ID
  inspection.
- [references/error-taxonomy.md](references/error-taxonomy.md): common
  `DB::Exception`, `HdxStorageError`, `ClusterError`, and infra errors mapped
  to root causes and first actions.
- [references/circuit-breakers.md](references/circuit-breakers.md):
  guardrails, per-query settings, spill-to-disk, pool, and concurrency knobs.
- [references/query-rewrite-patterns.md](references/query-rewrite-patterns.md):
  time pruning, column projection, indexes, latest-N lookups, summary tables,
  and query caching.

## External Docs

Use external Hydrolix docs only when the bundled references are insufficient or
you need to verify current product behavior:

- [Query Performance Debugging](https://docs.hydrolix.io/latest/exploring-data/query-troubleshooting/query-performance-debugging/)
- [Active Queries](https://docs.hydrolix.io/latest/exploring-data/query-troubleshooting/active-queries/)
- [Query Options Reference](https://docs.hydrolix.io/latest/exploring-data/query-interfaces/query-options-1/query-options-reference/)
- [HTTP Response Headers](https://docs.hydrolix.io/latest/exploring-data/query-troubleshooting/query-http-response-headers/)
- [MCP Server](https://docs.hydrolix.io/latest/exploring-data/query-interfaces/mcp-server/)
- [Writing Efficient Queries](https://docs.hydrolix.io/latest/exploring-data/query-guidance/writing-efficient-queries/)
