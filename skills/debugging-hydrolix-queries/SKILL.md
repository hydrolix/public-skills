---
name: debugging-hydrolix-queries
description: >
  Diagnose and tune slow, failing, or resource-heavy Hydrolix SQL queries by
  classifying errors, capturing X-HDX-Query-Stats or hdx.active_queries data,
  mapping circuit breakers to safe first actions, and choosing query rewrites or
  scoped SETTINGS before persistent changes. Use when a Hydrolix query is timing
  out, OOMing, returning DB::Exception or HdxStorageError, hitting a circuit
  breaker (max_timerange, max_partitions, max_columns, max_result_rows/bytes),
  being canceled by the query head, or running slower than expected over MCP,
  the HTTP Query API, or another SQL client.
---

# Debugging Hydrolix Queries

Most Hydrolix performance tickets are pruning or projection failures, not
capacity failures. Classify the error or capture stats first; rewrite or
filter the query before raising any limit, and never apply persistent Config
API changes without an operator-approved rollback value.

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

## Common Mistakes

| Mistake | Fix |
|---|---|
| Reading the full stack trace | Read only the first 1–2 error lines (rest is noise) |
| Raising a limit before narrowing the query | Try time-range tightening / explicit column projection first |
| Applying persistent Config API changes from a single failure | Use per-query `SETTINGS` first; persistent scope requires operator approval + rollback value |
| `SELECT *` on wide tables or summary tables | Project explicit columns; on summary tables, select published aliases, not internal aggregate-state columns |
| Assuming `LIMIT` reduces scan cost for `GROUP BY` / `ORDER BY` aggregates | Verify `limit_optimization = order_by_limit_n` in query stats before assuming early termination |
| Using bundled scripts to talk to the cluster | Scripts only parse pasted input — never connect, never mutate settings |
| Disabling `hdx_query_timerange_required` to make an error go away | Add the time filter instead — that guardrail is the cluster's primary partition-pruning protection |

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

## Rationalization Table

| Excuse | Reality |
|---|---|
| "Just raise the timeout, the query is legit" | Most `Code: 159` failures are pruning failures. Raise only after `WHERE` is verifiably selective via stats. |
| "User is in a hurry, I'll set it at project scope" | Persistent scope requires explicit operator approval + a captured rollback value. Per-query `SETTINGS` first, always. |
| "`hdx_query_timerange_required` is annoying, disable it for this dashboard" | This is the cluster's primary partition-pruning guardrail. Add the time filter instead. |
| "I'll preemptively spill-to-disk" | Spill increases runtime and local disk pressure. Reach for it after `Code: 241`, not before. |
| "The error stack is long, I should read it all" | Hydrolix errors are diagnostic in the first 1–2 lines. Everything below is C++ stack noise. |

## Red Flags — STOP and reconsider

- About to recommend a Config API change without capturing the current value and a rollback value
- About to raise a limit before reading `query_stats` or `hdx.active_queries`
- About to read past line 2 of an error message
- About to use a bundled script to make a network call or mutate settings
- About to apply a `SETTINGS` change at org/project/table scope on first failure
- About to recommend `SELECT *` survives because "the table isn't that wide"

## Reference Map

- `scripts/classify_error.py` — classifier for error fragments
- `scripts/summarize_query_stats.py` — query stats summarizer
- `references/error-taxonomy.md`
- `references/active-queries.md`
- `references/capture-query-stats.md`
- `references/circuit-breakers.md`
- `references/query-rewrite-patterns.md`

## External Docs

Use external Hydrolix docs only when the bundled references are insufficient or
you need to verify current product behavior:

- [Query Performance Debugging](https://docs.hydrolix.io/latest/exploring-data/query-troubleshooting/query-performance-debugging/)
- [Active Queries](https://docs.hydrolix.io/latest/exploring-data/query-troubleshooting/active-queries/)
- [Query Options Reference](https://docs.hydrolix.io/latest/exploring-data/query-interfaces/query-options-1/query-options-reference/)
- [HTTP Response Headers](https://docs.hydrolix.io/latest/exploring-data/query-troubleshooting/query-http-response-headers/)
- [MCP Server](https://docs.hydrolix.io/latest/exploring-data/query-interfaces/mcp-server/)
- [Writing Efficient Queries](https://docs.hydrolix.io/latest/exploring-data/query-guidance/writing-efficient-queries/)
