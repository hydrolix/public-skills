# bot-insights — Edge and Operations Analysis Patterns

Edge/Ops analysis runs against the deployed posture summaries `bi_summary_*`.
Path-grain and resource-grain aggregates referenced by older iterations
(`bot_agg_path_*`, `bot_agg_resource_*`, `bot_agg_ua_*`) and the request-level
`bot_detection` tables are **not currently deployed** — see
[data-model.md](data-model.md). Request-level fields that aren't retained in
`bi_summary_*` (exact query string, exact user agent, response-byte
attribution) should be surfaced as a limitation in the artifact, not as a
fallback query against a non-deployed table.

In SQL templates, replace `<posture_summary_hour>` with `bi_summary_hour` for
the Akamai/TrafficPeak project, or with the metadata-confirmed equivalent for
the cluster you're querying.

For structured cache-busting, query-string churn, cache-miss movement, or
origin-impact detector output, use
[cache-origin-impact.md](cache-origin-impact.md) first. That reference defines
the path-grain-only `cache_origin_impact_report.v1` boundary, confidence rules,
and metadata-aware SQL template requirements. This page remains a broader
Edge/Ops pattern reference.

## Contents

- [Producer Orchestration](#producer-orchestration)
- [Cache-Busting and Querystring Churn Detection](#cache-busting-and-querystring-churn-detection-edgeops)
- [Origin Impact and Bandwidth Cost](#origin-impact-and-bandwidth-cost-edgeops)

## Producer Orchestration

Use `bot_insights_report.py --report edge_ops_impact` to produce a
deterministic Edge & Origin Cost wrapper end-to-end. The producer
runs two Hydrolix queries in sequence, both gated by the data
firewall (local credentials → no LLM↔database round-trip, or a
handoff packet for the agent to run via MCP and resume):

1. **Entity-grain** query against `bi_summary_<granularity>` produces
   per-entity scorecard cards via `scorecard.py`. Supported entity
   types: `client_asn`, `request_host`, `bot_class`.
2. **Path-grain** query against `bot_agg_path_<granularity>` produces
   path candidates via `cache_origin_impact.py`. This step is gated
   on `--include-paths` because `bot_agg_path_*` is not currently
   deployed on production clusters. Optional `--host` flag scopes
   path candidates to a single request_host when the flag is set.

When local credentials resolve from
`~/.config/hydrolix/clusters/<cluster>/*.env`, both queries execute
directly and emit a `bot_report_input.v1` wrapper. When credentials
are absent, the script emits two handoff packets sequentially with
`report_context.artifact` annotations (`"scorecard"` then `"path"`)
that the agent resumes via `--raw-input` and `--raw-path-input`.

Path-grain failure (table missing, query error, zero rows) is
non-fatal: the wrapper ships with the entity-grain artifact only.
The renderer suppresses the Top Paths section when path candidates
are absent.

See [cache-origin-impact.md](cache-origin-impact.md) for the
path-grain detector contract and required input row shape.

### Cache-Busting and Querystring Churn Detection [Edge/Ops]

Bots that append unique query strings to every request defeat cache key
matching, causing artificial cache misses and origin overload.

Query-string cardinality (`uniq_qs`) and request-level query strings are not
retained in deployed summaries. Use `bi_summary_*` to spot the symptom — cache
miss share moving on a host, request-path-pattern, or bot-class slice — and
then run the structured detector via
[cache-origin-impact.md](cache-origin-impact.md) once path-grain aggregates are
available (gated on `--include-paths`; see
[edge_ops_impact orchestration](#producer-orchestration)).

```sql
-- Cache miss movement by host and bot-class from deployed posture summaries.
SELECT
    reqHost,
    isBotTraffic,
    sum(cnt_all) AS requests,
    sum(cnt_cache_miss) AS cache_misses,
    round(sum(cnt_cache_miss) / greatest(sum(cnt_all), 1) * 100, 2) AS miss_rate_pct
FROM <project>.bi_summary_hour
WHERE reqTimeSec >= now() - INTERVAL 24 HOUR
GROUP BY reqHost, isBotTraffic
HAVING requests > 100
ORDER BY miss_rate_pct DESC
LIMIT 20
```

Exact query-string diversity by ASN is a request-level dimension. The
request-level tables are not currently deployed; state that limitation rather
than substituting a non-deployed table.

### Origin Impact and Bandwidth Cost [Edge/Ops]

Origin latency, cache impact, and bandwidth attribution at the deployed grain
all run against `bi_summary_*`. Path-grain endpoint ranking (`request_path_norm
× bot_class` with p95 origin TTFB) and request-level byte attribution
(`response_total_bytes` per bot class) depend on `bot_agg_path_*` and
`bot_detection`, which are not currently deployed. Treat the entity-grain
output below as the supported surface; surface those richer dimensions as
limitations rather than substituting a non-deployed table.

```sql
-- Origin latency and cache impact by host and bot flag from posture summaries.
SELECT
    reqHost,
    isBotTraffic,
    sum(cnt_all) AS requests,
    avg(avg_origin_ttfb) AS avg_origin_ttfb,
    max(p95_origin_ttfb) AS p95_origin_ttfb,
    round(sum(cnt_cache_miss) / greatest(sum(cnt_all), 1) * 100, 2) AS cache_miss_pct
FROM <project>.bi_summary_hour
WHERE reqTimeSec >= now() - INTERVAL 24 HOUR
GROUP BY reqHost, isBotTraffic
ORDER BY requests DESC

-- Cache impact of bot traffic from posture summaries.
SELECT
    isBotTraffic,
    sum(cnt_all) AS requests,
    sum(cnt_cached) AS cache_hits,
    round(sum(cnt_cached) / greatest(sum(cnt_all), 1) * 100, 2) AS hit_rate_pct,
    sum(cnt_cache_miss) AS cache_misses
FROM <project>.<posture_summary_hour>
WHERE reqTimeSec >= now() - INTERVAL 24 HOUR
GROUP BY isBotTraffic
ORDER BY requests DESC
```

For path-grain origin cost ranking when `bot_agg_path_*` becomes available,
use `bot_insights_report.py --report edge_ops_impact --include-paths`; the
path-grain detector contract lives in
[cache-origin-impact.md](cache-origin-impact.md).
