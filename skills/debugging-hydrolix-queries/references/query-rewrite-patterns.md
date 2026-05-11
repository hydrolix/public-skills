# Query Rewrite Patterns

Read this reference when a query is slow but there is no single concrete error
that already explains the failure.

Most slow-query tickets are really pruning failures. Before raising limits,
make the query cheaper.

## Use the Primary Timestamp

Hydrolix partitions by primary timestamp. A filter such as
`WHERE ts >= ... AND ts < ...` lets Hydrolix skip partitions. Missing or very
wide time filters are the most common reason for high `num_partitions`.

## Project Columns Explicitly

`SELECT *` forces every column's dictionary and index to load. On wide tables,
this is a common cause of slow queries and OOMs. Name only the needed columns.

## Filter on Indexed or Shard-Key Columns

Check `index_stats.indexes_used` in `X-HDX-Query-Stats` or
`hdx.active_queries.query_stats` to confirm the planner used the expected
indexes and shard keys.

## Latest-N Raw Rows

For latest-N lookups, prefer `LIMIT` with `ORDER BY <primary_ts> DESC`.
Hydrolix can terminate early when `hdx_query_optimize_order_by_primary` is
effective. Look for `limit_optimization = order_by_limit_n` in query stats.

Do not assume `LIMIT` reduces scan cost for `GROUP BY`, `ORDER BY`, or
aggregate queries unless debug stats prove early termination was used.

## Summary Tables

Prefer matching summary tables for repeated aggregate dashboards when one
exists and its published aliases fit the requested aggregate. Summary tables
shift work to ingest, merge, and indexing, and have special query rules.

Do not use `SELECT *` on summary tables. Inspect the summary aliases and select
published aliases rather than internal aggregate-state columns.

## Query Cache

For repetitive dashboard queries, use query caching:

```sql
SELECT ...
SETTINGS
  use_query_cache = 1,
  query_cache_ttl = 60;
```
