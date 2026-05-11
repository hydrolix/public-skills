# Error Taxonomy

Read this reference when a Hydrolix query has already failed. Read the first
one or two error lines; the rest is usually stack trace noise.

For a deterministic first pass, run:

```bash
scripts/classify_error.py "Code: 159 DB::Exception: Timeout exceeded ..."
```

| Error fragment | Root cause | First thing to try |
| --- | --- | --- |
| `Code: 159 ... Timeout exceeded: elapsed N seconds, maximum: M` | `hdx_query_max_execution_time` circuit breaker. | Narrow the `WHERE` time range; add filters on indexed columns; raise the per-query limit only if the query is legitimate. |
| `Code: 241 ... Memory limit (for query) exceeded` | Per-query RAM cap on `query-head` or `query-peer`. | Enable spill-to-disk for this query; reduce `GROUP BY` cardinality; replace `SELECT *` with explicit columns. |
| `Code: 161 ... Limit for number of columns to read exceeded. Requested: N, maximum: M` | `hdx_query_max_columns_to_read`, usually from `SELECT *` on wide tables. | Project only needed columns, or raise the limit for the caller. |
| `Code: 396 ... Limit for result exceeded, max rows: X` | `hdx_query_max_result_rows`; response payload has too many rows. | Add `LIMIT` for raw-row output or pre-aggregate; raise only if the client and cluster can handle it. |
| `Code: 396 ... Limit for result exceeded, max bytes: X` | `hdx_query_max_result_bytes`; response payload too large by bytes. | Prefer aggregation over raw-row dumps. Config API org/project/table values must be at least `10000`. |
| `HdxStorageError Maximum time range exceeded for query: N seconds (maximum is M)` | `hdx_query_max_timerange_sec`. | Shorten the primary timestamp filter; raise the limit if the wider scan is intended. |
| `HdxStorageError hdx_query_timerange_required is set to true. Your query needs a time range filter ...` | No filter on the primary timestamp column. | Add `WHERE <primary_ts> BETWEEN ... AND ...`. Do not disable this guardrail casually. |
| `HdxStorageError Maximum number of partitions exceeded for query: N partitions` | `hdx_query_max_partitions`. | Tighten the time range or add a shard-key filter so the planner prunes partitions; raise only as a last resort. |
| `HdxStorageError Maximum number of rows exceeded for query` | `hdx_query_max_rows`; rows scanned, not rows returned. | Add a more selective `WHERE`; use a matching summary table if one exists. |
| `HdxStorageError No peers available to run query in pool` | Target query pool is empty or scaled to 0. | Check `hdx_query_pool_name`; verify the pool's `query-peer` replica count. |
| `ClusterError Pool name <X> does not exist` | Typo or pool was renamed. | List pools; fix `hdx_query_pool_name`. |
| `CatalogError Failed to submit transaction: ERROR: canceling statement due to statement timeout` | `hdx_query_catalog_timeout_ms`; catalog lookup slow. | Retry. If recurrent, raise with the cluster operator. |
| `DB::Exception: Database <x> doesn't exist` / `Table _local.XXXX does not exist` | Missing project qualifier, project/table typo, or quoting issue. | Fully qualify as `project.table`. Backtick hyphenated names: `` `my-project`.`my-table` ``. |
| `DB::NetException: Timeout: connect timed out` / `No route to host` | Infrastructure-level connectivity issue. | Retry. If persistent, escalate to the cluster operator; this is rarely query authoring. |
