# bot-insights - Cache-Origin Impact Detector

Use this reference when a Bot Insights request asks for structured
cache-busting, query-string churn, cache-miss movement, or origin-impact
evidence. The v1 detector output is `cache_origin_impact_report.v1`.

> **Deployment note**: The path-grain summary tables this detector requires
> (`bot_agg_path_day`, `bot_agg_path_hour`, `bot_agg_path_minute`) are **not
> currently deployed** on production clusters. The `edge_ops_impact` report
> only invokes the path-grain code path when called with `--include-paths`;
> without that flag the report ships entity-grain evidence only. Treat this
> reference as the contract for the opt-in path-grain detector and for future
> path-summary deployments.

This detector is deterministic and evidence-first. It ranks aggregate path
slices where query-string diversity, cache misses, and origin pressure move
together. It may identify a cache-busting candidate, origin-impact candidate, or
bot-attributable cache-miss candidate. It must not claim causality, classify
traffic with opaque models, recommend mitigations, or treat proxy origin
pressure as a billing or capacity unit.

## Contents

- [V1 Scope](#v1-scope)
- [Input Contract](#input-contract)
- [Standalone Script Example](#standalone-script-example)
- [Output Shape](#output-shape)
- [Confidence Boundary](#confidence-boundary)
- [Summary SQL Template Guidance](#summary-sql-template-guidance)
- [Tight Request-Level Query](#tight-request-level-query)

## V1 Scope

V1 is path-grain only. Produce one combined candidate list for one host scope,
one selected path-summary surface, and one selected path-grain dimension set.

Supported path-summary tables:

- `bot_agg_path_day`
- `bot_agg_path_hour`
- `bot_agg_path_minute`

Supported v1 dimension sets:

- `request_host + request_path_norm`
- `request_host + request_path_norm + bot_class`
- `request_host + request_path_norm + asn_type`
- `request_host + request_path_norm + bot_class + asn_type`

Supported detector families:

- Query-string diversity.
- Cache miss movement.
- Origin pressure movement.
- Bot-attributable misses and origin pressure.
- Optional response-byte metadata when upstream aggregate rows provide it.

Known v1 non-goals and future surfaces:

- Host-only candidate lists.
- Resource-category candidate lists from `bot_agg_resource_*`.
- ASN, CDN, bot-owner, exact-status, country, edge POP, user-agent, bot
  confidence, bot intent, or verified-owner candidate lists.
- Multiple independent candidate lists in one report.
- SIEM candidate surfaces.

`bi_summary_*` tables may be queried separately for
host-scope context such as bot-traffic share or AI-category share. That context
belongs under `optional_metadata.summary_context`; it must not be presented
as path-level candidate evidence.

## Input Contract

Local detector scripts accept aggregate JSON only. They may consume dictionary
rows, MCP-style `columns` plus `rows`, saved JSON files, pasted JSON, stdin, or
trusted in-process objects from a reviewed wrapper. They must not query
Hydrolix, read credentials, hold connection configuration, or process raw
request-level logs at scale.

Minimum useful input:

- `analysis_type` or `metric`
- `table_used`
- `granularity`
- `dimensions`
- `current_window`
- `baseline_windows` unless running current-only screening
- `metric_semantics`
- `rows`

Rows should provide already-aggregated current and baseline metrics. Common
canonical fields include:

- `current_requests`, `baseline_requests`
- `current_cache_misses`, `baseline_cache_misses`
- `current_unique_query_strings`, `baseline_unique_query_strings`
- `current_origin_p95_ms`, `baseline_origin_p95_ms`
- `cache_miss_contribution_pct`
- `origin_pressure_contribution_pct`
- `bot_miss_share_pct`
- `bot_origin_pressure_share_pct`

Metric semantics are required when rows include query-string cardinality,
origin-latency percentiles, or precomputed contribution fields. Missing
optional detector metrics should become `not_evaluated` entries, not zero-valued
evidence. Response-byte data is optional metadata and does not affect v1
candidate eligibility or scoring.

## Standalone Script Example

Use the local script after a Hydrolix MCP server or host Hydrolix query tool has
already returned aggregate rows. For example:

`uv run python skills/bot-insights/scripts/cache_origin_impact.py --file cache-origin-input.json`

The script reads JSON from a file, stdin, or a positional argument. It does not
query Hydrolix, validate credentials, prove causality, or recommend
mitigations.

Complete input example:

```json
{
  "analysis_type": "cache_busting_origin_impact",
  "comparison_type": "previous_window",
  "granularity": "hour",
  "table_used": "bot_agg_path_hour",
  "summary_table_used": true,
  "scope": {
    "request_host": "www.example.com",
    "selected_bot_classes": [
      "bad",
      "unknown"
    ]
  },
  "dimensions": [
    "request_path_norm",
    "bot_class",
    "asn_type"
  ],
  "current_window": {
    "start": "2026-04-18T12:00:00Z",
    "end": "2026-04-18T18:00:00Z"
  },
  "baseline_windows": [
    {
      "start": "2026-04-18T06:00:00Z",
      "end": "2026-04-18T12:00:00Z",
      "label": "previous_6_hours"
    }
  ],
  "metric_semantics": {
    "unique_query_strings": "exact_period_unique",
    "origin_p95_ms": "metadata_merged_quantile",
    "contribution_fields": "complete_scope_pre_limit"
  },
  "rows": [
    {
      "request_path_norm": "/api/search",
      "bot_class": "bad",
      "asn_type": "hosting",
      "current_requests": 10000,
      "baseline_requests": 10000,
      "current_cache_misses": 9000,
      "baseline_cache_misses": 7000,
      "current_unique_query_strings": 8500,
      "baseline_unique_query_strings": 4500,
      "current_origin_p95_ms": 360,
      "baseline_origin_p95_ms": 120,
      "current_total_cache_misses_for_share": 10000,
      "current_selected_bot_class_cache_misses_for_share": 9000,
      "current_total_origin_pressure_for_path": 3600,
      "current_selected_bot_class_origin_pressure_for_path": 3240,
      "current_total_cache_misses_for_contribution": 20000,
      "current_total_origin_pressure_for_contribution": 18000,
      "cache_miss_contribution_pct": 45,
      "origin_pressure_contribution_pct": 18
    }
  ]
}
```

Shortened output example, shown as a JSON fragment:

```json
{
  "schema_version": "cache_origin_impact_report.v1",
  "analysis_type": "cache_busting_origin_impact",
  "source_skill": "bot-insights",
  "comparison_type": "previous_window",
  "granularity": "hour",
  "table_used": "bot_agg_path_hour",
  "summary_table_used": true,
  "scope": {
    "request_host": "www.example.com",
    "selected_bot_classes": [
      "bad",
      "unknown"
    ]
  },
  "metric_semantics": {
    "origin_pressure_score": "proxy_misses_times_origin_p95_seconds",
    "unique_query_strings": "exact_period_unique",
    "origin_p95_ms": "metadata_merged_quantile",
    "contribution_fields": "complete_scope_pre_limit"
  },
  "candidates": [
    {
      "entity": {
        "request_path_norm": "/api/search",
        "bot_class": "bad",
        "asn_type": "hosting"
      },
      "current": {
        "requests": 10000,
        "cache_misses": 9000,
        "unique_query_strings": 8500,
        "origin_p95_ms": 360,
        "miss_rate_pct": 90,
        "qs_diversity_ratio": 0.85,
        "origin_pressure_score": 3240,
        "bot_miss_share_pct": 90,
        "bot_origin_pressure_share_pct": 90
      },
      "baseline": {
        "requests": 10000,
        "cache_misses": 7000,
        "unique_query_strings": 4500,
        "origin_p95_ms": 120,
        "miss_rate_pct": 70,
        "qs_diversity_ratio": 0.45,
        "origin_pressure_score": 840
      },
      "deltas": {
        "cache_misses": 2000,
        "miss_rate_delta_pp": 20,
        "qs_diversity_delta": 0.4,
        "origin_p95_delta_ms": 240,
        "origin_pressure_delta": 2400,
        "cache_miss_contribution_pct": 45,
        "origin_pressure_contribution_pct": 18
      },
      "candidate_score": 100,
      "candidate_band": "high",
      "finding_types": [
        "cache_busting_candidate",
        "cache_miss_movement_candidate",
        "origin_impact_candidate",
        "bot_attributable_cache_misses",
        "bot_attributable_origin_pressure"
      ],
      "confidence": "medium",
      "confidence_reasons": [
        "complete_scope_contribution",
        "origin_latency_merge_exact",
        "path_summary_used",
        "query_string_cardinality_exact",
        "retained_dimensions_fit",
        "summary_table_used"
      ],
      "limitations": [
        "response_byte_metadata_not_available"
      ],
      "rank": 1
    }
  ],
  "interpretation_constraints": [
    "mechanical_candidate_only",
    "no_causal_claim",
    "origin_pressure_score_is_proxy",
    "not_a_billing_or_capacity_unit",
    "llm_may_summarize_structured_evidence_only"
  ],
  "confidence": "medium",
  "limitations": [
    "response_byte_metadata_not_available"
  ]
}
```

## Output Shape

`cache_origin_impact_report.v1` should contain:

- `schema_version: "cache_origin_impact_report.v1"`
- `analysis_type: "cache_busting_origin_impact"`
- source metadata such as `source_skill`, `comparison_type`, `granularity`,
  `table_used`, `summary_table_used`, `scope`, `current_window`, and
  `baseline_windows`
- `metric_semantics`
- one combined `candidates` list
- candidate `entity`, `current`, `baseline`, `deltas`, and
  `share_denominators`
- `candidate_score`, `candidate_band`, `features`, and `finding_types`
- `confidence` and `confidence_reasons`
- `optional_metadata` for response bytes or host-scope bot-summary context
- `limitations`, `not_evaluated`, and `interpretation_constraints`

The score must expose feature contributions instead of hiding logic in prose.
Common finding types include `cache_busting_candidate`,
`origin_impact_candidate`, and `bot_attributable_cache_misses`.

## Confidence Boundary

Standalone saved, pasted, file, stdin, or ordinary MCP-shaped JSON cannot prove
provenance. Standalone mode must cap confidence at `medium`, even if the JSON
contains caller-supplied trust fields.

`high` confidence is only available to a reviewed skill-controlled in-process
wrapper that has inspected table metadata, selected retained dimensions,
generated metadata-correct SQL, executed through the Hydrolix query layer,
received the result object directly, computed or preserved complete-scope
denominators, and passed trusted context to the normalizer without serializing
that trust through caller-editable JSON.

Use confidence reasons such as:

- `summary_table_used`
- `path_summary_used`
- `retained_dimensions_fit`
- `query_string_cardinality_exact`
- `query_string_cardinality_approximate`
- `origin_latency_merge_exact`
- `origin_latency_worst_bucket`
- `baseline_duration_normalized`
- `current_count_sufficient`
- `baseline_count_sufficient`
- `sparse_counts`
- `complete_scope_contribution`
- `contribution_withheld_source_limited`
- `partial_current_bucket`
- `direct_mcp_trusted_context`
- `caller_supplied_json_confidence_cap`

## Summary SQL Template Guidance

> **Deployment-availability check first.** `bot_agg_path_day`,
> `bot_agg_path_hour`, and `bot_agg_path_minute` are **not currently
> deployed** on observed clusters (see the Deployment note at the top of
> this file). Do not generate SQL against them as a default — confirm the
> target cluster retains the path-summary surface, or apply the
> deployment-availability rule (SKILL.md) and ship entity-grain
> `bi_summary_*` evidence via `edge_ops_impact` instead. The template below
> is the contract for when path summaries are deployed.

Prefer the narrowest path summary that retains the requested dimensions:

- Use `bot_agg_path_day` for long-window path-level cache/origin movement.
- Use `bot_agg_path_hour` for hourly investigations and same-hour baselines.
- Use `bot_agg_path_minute` for short incident windows or change-review detail.

Before querying a deployed summary table, inspect table metadata with the
Hydrolix MCP server or host Hydrolix query tool. If a column is stored as an
aggregate state, query it with the exact merge function reported by metadata.
Do not infer `sumMerge`, `uniqMerge`, `quantilesMerge`, or `maxMerge` from a
column name.

Metadata-aware template for `bot_agg_path_day`, `bot_agg_path_hour`, or
`bot_agg_path_minute`:

```sql
-- Metadata-aware example:
-- 1. Inspect <project>.bot_agg_path_hour before generating this query.
-- 2. Replace plain aggregate expressions with metadata-provided merge
--    expressions when columns are aggregate states.
-- 3. Compute contribution and share denominators before applying LIMIT.
WITH
  toDateTime('<current_start>') AS current_start,
  toDateTime('<current_end>') AS current_end,
  toDateTime('<baseline_start>') AS baseline_start,
  toDateTime('<baseline_end>') AS baseline_end,
  ['bad'] AS selected_bot_classes,
  by_entity AS (
    SELECT
      request_host,
      request_path_norm,
      bot_class,
      asn_type,
      sumIf(cnt_all, timestamp >= current_start AND timestamp < current_end) AS current_requests,
      sumIf(cnt_all, timestamp >= baseline_start AND timestamp < baseline_end) AS baseline_requests,
      sumIf(cnt_cache_miss, timestamp >= current_start AND timestamp < current_end) AS current_cache_misses,
      sumIf(cnt_cache_miss, timestamp >= baseline_start AND timestamp < baseline_end) AS baseline_cache_misses,
      sumIf(uniq_qs, timestamp >= current_start AND timestamp < current_end) AS current_unique_query_strings,
      sumIf(uniq_qs, timestamp >= baseline_start AND timestamp < baseline_end) AS baseline_unique_query_strings,
      maxIf(p95_origin_ttfb, timestamp >= current_start AND timestamp < current_end) AS current_origin_p95_ms,
      maxIf(p95_origin_ttfb, timestamp >= baseline_start AND timestamp < baseline_end) AS baseline_origin_p95_ms
    FROM <project>.bot_agg_path_hour
    WHERE timestamp >= baseline_start
      AND timestamp < current_end
      AND request_host = '<host>'
    GROUP BY request_host, request_path_norm, bot_class, asn_type
  ),
  with_denominators AS (
    SELECT
      request_host,
      request_path_norm,
      bot_class,
      asn_type,
      current_requests,
      baseline_requests,
      current_cache_misses,
      baseline_cache_misses,
      current_unique_query_strings,
      baseline_unique_query_strings,
      current_origin_p95_ms,
      baseline_origin_p95_ms,
      current_cache_misses * greatest(current_origin_p95_ms, 1) / 1000 AS current_origin_pressure_score,
      sum(current_cache_misses) OVER (PARTITION BY request_path_norm) AS current_total_cache_misses_for_share,
      sumIf(current_cache_misses, has(selected_bot_classes, bot_class))
        OVER (PARTITION BY request_path_norm) AS current_selected_bot_class_cache_misses_for_share,
      sum(current_cache_misses) OVER () AS current_total_cache_misses_for_contribution,
      sum(current_cache_misses * greatest(current_origin_p95_ms, 1) / 1000)
        OVER (PARTITION BY request_path_norm) AS current_total_origin_pressure_for_path,
      sumIf(
        current_cache_misses * greatest(current_origin_p95_ms, 1) / 1000,
        has(selected_bot_classes, bot_class)
      ) OVER (PARTITION BY request_path_norm) AS current_selected_bot_class_origin_pressure_for_path,
      sum(current_cache_misses * greatest(current_origin_p95_ms, 1) / 1000) OVER () AS current_total_origin_pressure_score
    FROM by_entity
  )
SELECT
  request_host,
  request_path_norm,
  bot_class,
  asn_type,
  current_requests,
  baseline_requests,
  current_cache_misses,
  baseline_cache_misses,
  current_unique_query_strings,
  baseline_unique_query_strings,
  current_origin_p95_ms,
  baseline_origin_p95_ms,
  current_total_cache_misses_for_share,
  current_selected_bot_class_cache_misses_for_share,
  current_total_cache_misses_for_contribution,
  current_total_origin_pressure_for_path,
  current_selected_bot_class_origin_pressure_for_path,
  round(current_selected_bot_class_cache_misses_for_share / greatest(current_total_cache_misses_for_share, 1) * 100, 2) AS bot_miss_share_pct,
  round(current_selected_bot_class_origin_pressure_for_path / greatest(current_total_origin_pressure_for_path, 1) * 100, 2) AS bot_origin_pressure_share_pct,
  round(current_cache_misses / greatest(current_total_cache_misses_for_contribution, 1) * 100, 2) AS cache_miss_contribution_pct,
  round(current_origin_pressure_score / greatest(current_total_origin_pressure_score, 1) * 100, 2) AS origin_pressure_contribution_pct
FROM with_denominators
ORDER BY abs(current_cache_misses - baseline_cache_misses) DESC
LIMIT 50
```

This template is not a copy-paste contract. It shows the row shape expected by
the local normalizer. Production SQL generation must substitute
metadata-provided merge expressions for aggregate-state columns and must record
whether unique query-string and origin percentile semantics are exact,
approximate, or worst-bucket summaries.

## Tight Request-Level Query

Historical guidance was to fall back to a tight `bot_detection` query when a
required path-grain metric (exact query-string cardinality, request-level
percentile origin TTFB, response bytes) couldn't be answered from path
summaries. The `bot_detection` table is **not currently deployed** on
production clusters; surface that limitation in the detector output (for
example `query_string_cardinality_approximate` with a confidence reason
explaining the missing surface) rather than substituting a non-deployed
request-level query.
