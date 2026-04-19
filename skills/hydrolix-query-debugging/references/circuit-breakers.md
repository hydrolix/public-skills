# Circuit Breakers and Tuning Knobs

Read this reference before recommending or applying Hydrolix query settings,
especially persistent Config API changes.

Circuit breakers protect the cluster. The right fix is usually a more selective
query, not a higher limit. When a query is legitimate, test the narrowest
possible per-query change using SQL `SETTINGS`, HTTP query parameters, or the
`X-HDX-query-settings` header.

Do not make persistent Config API changes at org, project, or table scope
unless the user or cluster operator explicitly asks for that persistent change.
Before recommending or applying one, capture the current value, state the exact
scope, explain the risk, prefer the smallest scope, and include a rollback
value.

## Guardrails

Recommend at project level when appropriate; do not apply without approval.

| Setting | Default | Common recommendation |
| --- | --- | --- |
| `hdx_query_timerange_required` | `false` | `true`; forces every query to filter on the primary timestamp. |
| `hdx_query_max_timerange_sec` | `0` | Example: `2764800` for 32-day dashboards; adjust to retention and SLA. |
| `hdx_query_max_result_rows` | `0` | Example: `1000000`; stops runaway row dumps from crushing the client. |
| `hdx_query_max_execution_time` | `0` | Example: `120` seconds for interactive workloads; looser for batch pools. |

## Memory Mitigation

Use for `Code: 241` memory failures. Spill-to-disk lets Hydrolix survive some
queries bigger than RAM by writing intermediate `GROUP BY` or `ORDER BY` state
to local disk. It can increase local disk use and query runtime. Choose
percentage or bytes, not both.

```sql
SELECT domain, count() AS hits
FROM my_project.access_log
WHERE timestamp >= now() - INTERVAL 1 DAY
GROUP BY domain
SETTINGS
  hdx_query_max_perc_before_external_group_by = 60,
  hdx_query_max_perc_before_external_sort = 60;
```

## Concurrency and Parallelism

Use carefully when a peer is thrashing:

- `hdx_query_max_streams`: partition-reading threads per peer; `0` means CPU
  count.
- `hdx_query_max_concurrent_partitions`: partitions open per thread; default is
  `3`. Effective parallelism is roughly
  `max_streams * max_concurrent_partitions`. Values above `25` risk memory
  pressure.
- `hdx_query_max_peers`: restrict a query to a subset of peers.
- `hdx_query_pool_name`: route to a dedicated pool to avoid noisy neighbors.

Changing concurrency can trade one bottleneck for another. Use these settings
for a single diagnostic query first, and avoid broad persistent changes unless
the operator approved the scope and rollback plan.
