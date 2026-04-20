# Cache-Busting and Origin-Impact Detector Design

This document designs a deterministic cache-busting and origin-impact detector
for the `bot-insights` skill.

The detector should identify aggregate traffic slices where cache-key churn,
cache misses, and origin pressure move together. It should produce structured
evidence for an LLM to summarize. It should not make causal claims, classify
traffic with opaque models, recommend mitigations directly, or query Hydrolix
from local scripts.

## Source Proposal

This design expands the "Cache-Busting and Origin-Impact Detection" capability
from the Advanced Analytics proposals draft. The relevant proposal content is
preserved here so this v1 design package does not depend on a separate draft
document being present in the checkout.

For Bot Insights, the proposal maps to identifying bot-classified or
bot-adjacent client behavior that defeats caching and increases origin load.
Candidate detectors include:

- Query-string diversity ratio by path and ASN.
- Cache miss delta by path.
- Origin p95 or p99 delta by path.
- Origin cost score based on requests and origin tail latency.
- Bot-attributable cache misses.
- Bytes served to bots by class, owner, or category.

The same proposal requires mechanical outputs first: the LLM can explain
structured evidence, but it must not compute anomaly scores, infer causality,
or classify raw traffic.

## Problem

Bot-driven cache-busting investigations often start with a symptom such as
high origin load, degraded origin latency, or a falling cache hit rate. The
hard part is connecting that symptom to concrete Bot Insights dimensions:

- which host, normalized path, ASN, ASN type, bot class, AI category, resource
  category, method, or `hdx_cdn` source moved;
- whether the movement is ordinary volume growth or unusually high
  query-string cardinality;
- whether the affected traffic is reaching origin;
- whether bots explain a meaningful share of the misses or origin pressure;
- whether the evidence is summary-backed, raw-table fallback, sparse, or
  approximate.

The detector must make those distinctions mechanically and report limitations
explicitly.

## Goals

- Produce a stable `cache_origin_impact_report.v1` packet from aggregate rows.
- Prefer Bot Insights summary tables when their retained dimensions fit the
  question.
- Treat Bot Insights path summaries as the first-class v1 surface because they
  retain `request_path_norm`, `bot_class`, `asn_type`, cache misses, origin
  p95/p99, and `uniq_qs`.
- Allow a separate `bot_summary_*` contextual query when bot-attribution context
  needs retained fields that path summaries do not provide, such as
  `is_bot_traffic` or `ai_category`.
- Separate cache-busting evidence from origin-impact evidence while allowing a
  combined candidate score.
- Expose feature-level score contributions, confidence labels, confidence
  reasons, and limitations.
- Keep local code dependency-light and deterministic.
- Keep Hydrolix access in the MCP/query-tool layer. Local scripts accept MCP
  result JSON, saved JSON, pasted aggregate JSON, or rows already aggregated by
  SQL.

## Non-Goals

- No database clients, credentials, connection handling, or direct Hydrolix
  execution in local scripts.
- No raw request-level processing at scale inside Python.
- No opaque ML, clustering, deep learning, or unsupervised traffic
  classification in v1.
- No causal claims. The output may say a slice is a cache-busting candidate or
  origin-impact candidate, not that it caused the incident.
- No mitigation recommendations such as block, challenge, or cache-rule edits.
- No high-confidence trust from caller-editable JSON alone.
- No non-path-grain candidate detectors in v1. Resource, ASN, host-only, CDN,
  bot-owner, and exact-status candidate lists are known limitations and desired
  future features. Host-scope `bot_summary_*` rows may be used only as
  contextual evidence for bot-attribution fields not retained on path summaries.
- No new monthly or quarterly summaries for this feature until query
  benchmarks prove current day/hour summaries are insufficient.

## Existing State

`bot-insights` has request-level `bot_detection` and `bot_detection_siem`
tables plus multiple summaries. The v1 detector surface is path-grain only:

| Table | Use |
|-------|-----|
| `bot_agg_path_day` | Long-window path-level cache/origin and query-string diversity by `request_path_norm`, `bot_class`, and `asn_type`. |
| `bot_agg_path_hour` | Hourly path-level cache/origin and query-string diversity. |
| `bot_agg_path_minute` | Short incident or change-review detail. |

Related summary surfaces such as `bot_agg_resource_*`, `bot_agg_asn_hour`, and
`bot_siem_*` are out of scope for v1 detector output. `bot_summary_*` is allowed
as a separate host-scope contextual query for retained bot fields such as
`is_bot_traffic` and `ai_category`, but it must not create a second non-path
candidate list in v1. The other surfaces remain useful as future detector
grains, broad posture context, or collateral checks after path-grain detection
ships.

The Bot Insights summary references list these relevant metric aliases:

- `cnt_all`
- `cnt_cache_miss`
- `p95_origin_ttfb`
- `p99_origin_ttfb`
- optional `response_total_bytes` or source-specific byte equivalents when
  present in raw fallback rows or deployment-specific summary metadata
- `uniq_client_ip`
- `uniq_paths` for future ASN-level context
- `uniq_qs`
- `cnt_blocked`, `cnt_auth_fail`, and `cnt_biz_fail` for SIEM collateral
  context

Raw request-level fallback is required for future bot dimensions not retained
in the current summary catalog, such as `verified_bot_owner`, `bot_confidence`,
`bot_intent`, `bot_category`, `bot_type`, exact status code, attack data,
country, edge POP, and user-agent details. V1 only allows raw fallback when it
still emits one of the supported path-grain dimension sets.

## V1 Scope

V1 should detect and rank cache-origin candidates for one host scope, one
path-summary surface, and one selected path-grain dimension set per report.
It may also include one optional host-scope `bot_summary_*` context block for
bot fields unavailable on the selected path-summary surface.

Supported v1 dimension sets:

- `request_host + request_path_norm`
- `request_host + request_path_norm + bot_class`
- `request_host + request_path_norm + asn_type`
- `request_host + request_path_norm + bot_class + asn_type`

Known v1 limitations and desired future features:

- Host-only rollups.
- Resource-category rollups from `bot_agg_resource_*`.
- ASN and ASN-type rollups from `bot_summary_*` or `bot_agg_asn_hour`.
- CDN rollups by `hdx_cdn`.
- Bot-owner, confidence, intent, exact status, country, edge POP, and
  user-agent dimensions through raw fallback.
- Multiple independent candidate lists in one report.

Supported v1 detector families:

- Query-string diversity.
- Cache miss movement.
- Origin pressure movement.
- Bot-attributable misses and origin pressure.
- Optional response-byte metadata for high-impact candidates when upstream rows
  provide it. Missing response-byte metadata is not a detector failure in v1.

V1 should emit at most one combined candidate list plus optional detector-level
metric summaries.

## Data Selection

Use the narrowest summary table that retains the requested dimensions.

| Question | Preferred surface | Fallback |
|----------|-------------------|----------|
| Bot path query-string churn over a month | `bot_agg_path_day` | `bot_detection` with tight range if a missing dimension is required |
| Bot path query-string churn over hours | `bot_agg_path_hour` | `bot_agg_path_minute` for incident detail |
| Bot path origin p95 movement | `bot_agg_path_day/hour/minute` | `bot_detection` if exact raw dimensions are required |
| Bot class path cache/origin impact | `bot_agg_path_day/hour/minute` | `bot_detection` with tight range if a missing path-grain dimension is required |
| ASN-type path cache/origin impact | `bot_agg_path_day/hour/minute` | `bot_detection` with tight range if a missing path-grain dimension is required |
| Host-scope bot traffic or AI-category context for a path report | `bot_summary_day/hour/minute` | Omit context if retained dimensions or comparable windows do not fit |
| Resource, ASN, CDN, host-only, verified owner, confidence, intent, exact status, country, edge POP, or user-agent cache impact | Out of v1 detector scope | Future feature or separate contextual query |

Before querying summary tables, inspect table metadata. If a metric is stored
as an aggregate state, SQL must use the table metadata's merge function. The
detector design must not infer `sumMerge`, `uniqMerge`, or quantile merge
syntax from column names alone.

## Metric Definitions

The detector should normalize aggregate inputs into the following canonical
metric names.

| Metric | Formula | Notes |
|--------|---------|-------|
| `requests` | `cnt_all` | Additive. |
| `cache_misses` | `cnt_cache_miss` or `countIf(cache_was_cached = false)` | Additive. |
| `miss_rate_pct` | `cache_misses / requests * 100` | Guard with `requests > 0`. |
| `unique_query_strings` | `uniq_qs` or `uniqExact(request_query_string)` | Must record exact, approximate, or bucket-summed semantics. |
| `qs_diversity_ratio` | `unique_query_strings / requests` | Clamp to `0..1` only when uniqueness semantics are exact for the period. Otherwise leave as computed and mark approximate. |
| `origin_p95_ms` | summary p95 or raw `quantile(0.95)(origin_time_to_first_byte_ms)` | Must record exact merge, worst-bucket, or raw quantile semantics. |
| `origin_p99_ms` | summary p99 or raw `quantile(0.99)(origin_time_to_first_byte_ms)` | Same caveat as p95. |
| `origin_pressure_score` | `cache_misses * max(origin_p95_ms, 1) / 1000` | Proxy score, not a real cost unit. |
| `origin_pressure_delta` | current pressure minus normalized baseline pressure | Used for ranking and contribution. |
| `cache_miss_contribution_pct` | candidate cache misses / complete-scope current cache misses * 100 | Requires a complete-scope denominator computed before row limits, or a trusted precomputed percentage with basis metadata. |
| `bot_miss_share_pct` | selected bot-class cache misses for the path / all cache misses for the path * 100 | Requires `bot_class` on the path summary or raw fallback. If multiple bot classes are selected, sum their misses before dividing. |
| `bot_origin_pressure_share_pct` | selected bot-class origin pressure for the path / total origin pressure for the path * 100 | Proxy share, not causal attribution. If multiple bot classes are selected, sum their proxy pressure before dividing. |
| `response_bytes` | `response_total_bytes` or deployment-specific byte aggregate | Optional metadata only in v1; not required for scoring or candidate eligibility. |

For baseline comparison, normalize additive baseline metrics to the current
window duration when the windows are unequal, then compute derived metrics from
those normalized components. For example, compute baseline origin pressure from
duration-normalized baseline cache misses and the baseline p95 latency. Do not
duration-normalize rates or tail-latency values directly; compute rates from
normalized numerators and denominators, and compare latency values with their
own declared semantics. Do not duration-normalize unique counts such as
`unique_query_strings`; either compare equal-duration windows, compute
per-bucket diversity with approximate semantics, or mark query-string comparison
confidence down.

## Detector Logic

### Query-String Diversity Detector

Purpose: find slices where request volume is spread across many query-string
variants.

Canonical features:

- `requests`
- `unique_query_strings`
- `qs_diversity_ratio`
- `baseline_qs_diversity_ratio`
- `qs_diversity_delta`
- `cache_misses`
- `miss_rate_pct`

Default candidate guards:

- `current_requests >= 1000`
- `current_unique_query_strings >= 100`
- `qs_diversity_ratio >= 0.5`

Default strong-signal thresholds:

- `qs_diversity_ratio >= 0.8`
- `qs_diversity_delta >= 0.25`
- `miss_rate_pct >= 50`

If the unique query-string value is a sum of per-bucket unique counts rather
than an exact period-level unique count, the feature remains useful for trend
screening but must add `query_string_cardinality_approximate` to limitations.

### Cache Miss Movement Detector

Purpose: identify slices driving cache miss growth or high miss rates.

Canonical features:

- `current_cache_misses`
- `baseline_cache_misses`
- `cache_miss_delta`
- `cache_miss_pct_change`
- `current_miss_rate_pct`
- `baseline_miss_rate_pct`
- `miss_rate_delta_pp`
- `cache_miss_contribution_pct`

Default candidate guards:

- `current_requests >= 1000`
- `current_cache_misses >= 100`

Default strong-signal thresholds:

- `miss_rate_delta_pp >= 10`
- `cache_miss_pct_change >= 100`
- `cache_miss_contribution_pct >= 10`

Contribution percentages require complete-scope evidence. If Hydrolix applies
a source limit before denominator calculation, withhold contribution and report
`contribution_withheld_source_limited`. The normalizer may compute
`cache_miss_contribution_pct` from `current_cache_misses` and
`current_total_cache_misses_for_contribution`, or accept a precomputed
`cache_miss_contribution_pct` only when the payload declares
`contribution_basis: "complete_scope_pre_limit"`.

### Origin Pressure Detector

Purpose: rank slices where miss volume and origin latency combine into high
operational impact.

Canonical features:

- `origin_p95_ms`
- `baseline_origin_p95_ms`
- `origin_p95_delta_ms`
- `origin_p95_pct_change`
- `origin_pressure_score`
- `baseline_origin_pressure_score`
- `origin_pressure_delta`
- `origin_pressure_contribution_pct`

Default candidate guards:

- `current_cache_misses >= 100`
- `origin_p95_ms > 0`

Default strong-signal thresholds:

- `origin_p95_delta_ms >= 100`
- `origin_p95_pct_change >= 50`
- `origin_pressure_contribution_pct >= 10`

`origin_pressure_score` is an investigative proxy. The report must label it as
`proxy_unit: "misses_times_origin_p95_seconds"` and include
`not_a_billing_or_capacity_unit` in interpretation constraints.

### Bot-Attributable Impact Detector

Purpose: show whether bot-classified slices account for a meaningful share of
cache misses or origin pressure.

Canonical features:

- `selected_bot_classes`
- optional host-scope `is_bot_traffic` context from `bot_summary_*`
- optional host-scope `ai_category` context from `bot_summary_*`
- `bot_miss_share_pct`
- `bot_origin_pressure_share_pct`
- `bot_cache_miss_delta`
- `bot_origin_pressure_delta`

Default candidate guards:

- `current_cache_misses >= 100`
- `bot_miss_share_pct >= 25` or `bot_origin_pressure_share_pct >= 25`

Default strong-signal thresholds:

- `bot_miss_share_pct >= 50`
- `bot_origin_pressure_share_pct >= 50`
- `bot_cache_miss_delta >= 1000`

This detector can say that misses are associated with the selected bot class or
classes according to retained bot dimensions. `bot_miss_share_pct` is always
computed as selected bot-class misses divided by total misses for the same path;
if more than one class is selected, the numerator is the sum across those
classes. `bot_origin_pressure_share_pct` follows the same selected-class over
same-path denominator pattern using the proxy origin pressure score rather than
raw misses. Optional `bot_summary_*` context may describe host-scope bot traffic
or AI-category posture, but it must not be presented as path-level evidence. The
detector must not claim bot traffic caused the origin issue without external
evidence.

## Combined Candidate Score

V1 should use an explainable point score, capped at 100. The exact points are
configuration, but the output must show every contributing feature.

Default scoring:

| Feature | Points | Condition |
|---------|--------|-----------|
| `high_query_string_diversity` | 20 | `qs_diversity_ratio >= 0.8` |
| `moderate_query_string_diversity` | 10 | `0.5 <= qs_diversity_ratio < 0.8` |
| `query_string_diversity_increased` | 10 | `qs_diversity_delta >= 0.25` |
| `high_miss_rate` | 15 | `miss_rate_pct >= 80` |
| `miss_rate_increased` | 15 | `miss_rate_delta_pp >= 10` |
| `origin_tail_latency_increased` | 15 | `origin_p95_delta_ms >= 100` and `origin_p95_pct_change >= 50` |
| `origin_pressure_contributor` | 15 | `origin_pressure_contribution_pct >= 10` |
| `bot_attributable_majority` | 10 | `bot_miss_share_pct >= 50` or `bot_origin_pressure_share_pct >= 50` |
| `large_current_volume` | 5 | `current_requests >= 10000` |

Bands:

- `high`: score >= 70
- `medium`: score >= 45 and < 70
- `low`: score >= 20 and < 45
- `informational`: score < 20

Low-volume slices may be emitted as sparse candidates, but they should not be
ranked above volume-sufficient candidates unless the user explicitly asks for
small-volume outliers.

## Confidence

Confidence is a label plus machine-readable reasons.

Labels:

- `high`: only available through a reviewed skill-controlled direct-MCP path
  that receives table metadata and query output directly, uses summary or
  tightly scoped raw queries, proves retained dimensions fit, proves current
  and baseline counts meet minimums, and binds complete-scope evidence to the
  result payload.
- `medium`: well-formed saved or pasted aggregate rows, summary-backed rows
  without direct-MCP trust, approximate query-string uniqueness, approximate
  latency semantics, or one comparable baseline window.
- `low`: sparse counts, missing baseline, raw fallback for broad ranges,
  source-limited rowsets, partial current buckets, missing dimensions, or
  material source coverage caveats.

Common confidence reasons:

- `summary_table_used`
- `raw_table_fallback`
- `retained_dimensions_fit`
- `missing_retained_dimension`
- `path_summary_used`
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

Standalone scripts that read file, stdin, saved MCP JSON, pasted JSON, or
ordinary `columns`/`rows` JSON must cap confidence at `medium`, even if the
JSON contains fields claiming trusted provenance.

Caller-supplied JSON must not be allowed to self-attest `trusted_context` or
`direct_mcp_trusted_context`. A reviewed wrapper may pass trusted context as an
in-process object, separate from file/stdin JSON, after it has inspected table
metadata, executed the query, mapped rows, computed a digest, and verified
complete-scope evidence. The trusted context should include the table metadata
used for merge decisions, selected dimensions, query/result digest, and
rowset-completeness proof.

## Output Schema

The primary output schema is `cache_origin_impact_report.v1`.
The example below assumes the reviewed direct-MCP wrapper path described later in
this document; standalone saved or pasted JSON must cap confidence at `medium`.

```json
{
  "schema_version": "cache_origin_impact_report.v1",
  "analysis_type": "cache_busting_origin_impact",
  "source_skill": "bot-insights",
  "comparison_type": "previous_window",
  "granularity": "day",
  "table_used": "bot_agg_path_day",
  "summary_table_used": true,
  "scope": {"request_host": "www.example.com", "selected_bot_classes": ["bad"]},
  "current_window": {"start": "2026-03-04T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
  "baseline_windows": [
    {"start": "2026-02-04T00:00:00Z", "end": "2026-03-04T00:00:00Z", "label": "previous_28_days"}
  ],
  "baseline_normalization": {
    "method": "none_equal_duration_windows",
    "factor": 1.0,
    "applies_to": []
  },
  "metric_semantics": {
    "unique_query_strings": "exact_period_unique",
    "origin_p95_ms": "metadata_merged_quantile",
    "origin_pressure_score": "proxy_misses_times_origin_p95_seconds"
  },
  "candidates": [
    {
      "rank": 1,
      "entity": {
        "request_path_norm": "/api/search",
        "bot_class": "bad",
        "asn_type": "hosting"
      },
      "current": {
        "requests": 82000,
        "cache_misses": 77244,
        "miss_rate_pct": 94.2,
        "unique_query_strings": 79700,
        "qs_diversity_ratio": 0.971,
        "origin_p95_ms": 680,
        "origin_pressure_score": 52525.92,
        "bot_miss_share_pct": 99.1,
        "bot_origin_pressure_share_pct": 99.1
      },
      "baseline": {
        "requests": 21000,
        "cache_misses": 6300,
        "miss_rate_pct": 30.0,
        "unique_query_strings": 7000,
        "qs_diversity_ratio": 0.333,
        "origin_p95_ms": 220,
        "origin_pressure_score": 1386.0
      },
      "deltas": {
        "requests": 61000,
        "cache_misses": 70944,
        "miss_rate_delta_pp": 64.2,
        "qs_diversity_delta": 0.638,
        "origin_p95_delta_ms": 460,
        "origin_pressure_delta": 51139.92,
        "cache_miss_contribution_pct": 37.4,
        "origin_pressure_contribution_pct": 37.4
      },
      "share_denominators": {
        "bot_miss_share_basis": "selected_bot_classes_over_path_all_bot_classes_and_asn_types",
        "bot_origin_pressure_share_basis": "selected_bot_classes_over_path_all_bot_classes_and_asn_types",
        "selected_bot_classes": ["bad"],
        "current_total_cache_misses_for_share": 77945,
        "current_total_origin_pressure_for_path": 53003.0,
        "current_selected_bot_class_origin_pressure_for_path": 52525.92,
        "cache_miss_contribution_basis": "complete_scope_pre_limit",
        "current_total_cache_misses_for_contribution": 206535,
        "origin_pressure_contribution_basis": "complete_scope_pre_limit"
      },
      "candidate_score": 100,
      "candidate_band": "high",
      "features": [
        {"name": "high_query_string_diversity", "points": 20, "value": 0.971, "threshold": 0.8},
        {"name": "query_string_diversity_increased", "points": 10, "value": 0.638, "threshold": 0.25},
        {"name": "high_miss_rate", "points": 15, "value": 94.2, "threshold": 80},
        {"name": "miss_rate_increased", "points": 15, "value": 64.2, "threshold": 10},
        {"name": "origin_tail_latency_increased", "points": 15, "value": 460, "threshold": 100},
        {"name": "origin_pressure_contributor", "points": 15, "value": 37.4, "threshold": 10},
        {"name": "bot_attributable_majority", "points": 10, "value": 99.1, "threshold": 50}
      ],
      "finding_types": [
        "cache_busting_candidate",
        "origin_impact_candidate",
        "bot_attributable_cache_misses"
      ],
      "confidence": "high",
      "confidence_reasons": [
        "summary_table_used",
        "path_summary_used",
        "retained_dimensions_fit",
        "query_string_cardinality_exact",
        "origin_latency_merge_exact",
        "current_count_sufficient",
        "baseline_count_sufficient",
        "complete_scope_contribution",
        "direct_mcp_trusted_context"
      ],
      "optional_metadata": {
        "response_bytes": {
          "available": false,
          "reason": "not_present_in_selected_path_summary"
        },
        "bot_summary_context": {
          "available": true,
          "scope": {"request_host": "www.example.com"},
          "metrics": {
            "host_bot_traffic_share_pct": 42.1,
            "host_ai_category_share_pct": 7.4
          },
          "limitations": [
            "host_scope_context_not_path_level_evidence"
          ]
        }
      },
      "limitations": [
        "response_byte_metadata_not_available"
      ]
    }
  ],
  "not_evaluated": [
    {
      "metric": "verified_bot_owner",
      "reason": "non_path_grain_future_feature"
    }
  ],
  "interpretation_constraints": [
    "mechanical_candidate_only",
    "no_causal_claim",
    "origin_pressure_score_is_proxy",
    "not_a_billing_or_capacity_unit",
    "llm_may_summarize_structured_evidence_only"
  ]
}
```

## Input Contract For Local Normalization

The local normalizer should accept a JSON object with aggregate rows. It should
also accept MCP-style `columns` plus `rows` and map those into dictionaries.

Minimum useful input:

- `metric` or `analysis_type`
- `dimensions`
- `current_window`
- `baseline_windows` unless running current-only screening
- `metric_semantics` whenever rows contain query-string cardinality,
  origin-latency percentile, or precomputed contribution fields
- `rows`

Recommended row shape:

```json
{
  "schema_version": "cache_origin_impact_input.v1",
  "source_skill": "bot-insights",
  "comparison_type": "previous_window",
  "granularity": "hour",
  "table_used": "bot_agg_path_hour",
  "summary_table_used": true,
  "scope": {"request_host": "www.example.com", "selected_bot_classes": ["bad"]},
  "dimensions": ["request_path_norm", "bot_class"],
  "current_window": {"start": "2026-04-18T12:00:00Z", "end": "2026-04-18T18:00:00Z"},
  "baseline_windows": [
    {"start": "2026-04-11T12:00:00Z", "end": "2026-04-11T18:00:00Z", "label": "same_weekday_hour_last_week"}
  ],
  "rowset_complete": false,
  "contribution_basis": "complete_scope_pre_limit",
  "metric_semantics": {
    "unique_query_strings": "exact_period_unique",
    "origin_p95_ms": "metadata_merged_quantile",
    "origin_pressure_score": "proxy_misses_times_origin_p95_seconds",
    "contribution_fields": "complete_scope_pre_limit"
  },
  "rows": [
    {
      "request_path_norm": "/api/search",
      "bot_class": "bad",
      "current_requests": 82000,
      "baseline_requests": 21000,
      "current_cache_misses": 77244,
      "baseline_cache_misses": 6300,
      "current_unique_query_strings": 79700,
      "baseline_unique_query_strings": 7000,
      "current_origin_p95_ms": 680,
      "baseline_origin_p95_ms": 220,
      "current_total_cache_misses_for_share": 77945,
      "current_selected_bot_class_cache_misses_for_share": 77244,
      "current_total_cache_misses_for_contribution": 206535,
      "current_total_origin_pressure_for_path": 53003.0,
      "current_selected_bot_class_origin_pressure_for_path": 52525.92,
      "cache_miss_contribution_pct": 37.4,
      "origin_pressure_contribution_pct": 37.4,
      "bot_origin_pressure_share_pct": 99.1
    }
  ]
}
```

The normalizer should reject mixed row shapes, unsupported non-path-grain
dimensions, ambiguous metric aliases, missing dimension values, negative counts,
non-numeric numeric fields, impossible rate inputs, and missing
`metric_semantics` for any supplied cardinality, percentile, or contribution
field whose semantics affect confidence or scoring. Missing optional detector
metrics should produce `not_evaluated` entries, not zero-valued evidence.
Missing response-byte metadata should be omitted or represented under
`optional_metadata`; it should not create `not_evaluated` detector evidence.
If standalone JSON includes a `trusted_context` field, the normalizer must ignore
it for confidence purposes and add `caller_supplied_json_confidence_cap`.

## SQL Template Direction

SQL templates should live in skill references or a reviewed generator. They
should produce small aggregate result sets for the local normalizer.

For summary tables, templates must be metadata-aware:

- call the Hydrolix table metadata tool before query generation;
- use exact `merge_function` values for aggregate-state columns;
- quote function-like column names with backticks;
- compute current and baseline aggregates before applying final output limits;
- compute contribution and share denominators before limiting rows;
- expose whether unique counts and p95/p99 values are exact merge results,
  approximations, or worst-bucket summaries.

Conceptual bot path summary template:

```sql
WITH
  toDateTime('<current_start>') AS current_start,
  toDateTime('<current_end>') AS current_end,
  toDateTime('<baseline_start>') AS baseline_start,
  toDateTime('<baseline_end>') AS baseline_end,
  ['bad'] AS selected_bot_classes,
  by_entity AS (
    SELECT
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
    GROUP BY request_path_norm, bot_class, asn_type
  ),
  with_denominators AS (
    SELECT
      *,
      sum(current_cache_misses) OVER (PARTITION BY request_path_norm) AS current_total_cache_misses_for_share,
      sumIf(current_cache_misses, has(selected_bot_classes, bot_class))
        OVER (PARTITION BY request_path_norm) AS current_selected_bot_class_cache_misses_for_share,
      current_cache_misses * greatest(current_origin_p95_ms, 1) / 1000 AS current_origin_pressure_score,
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

This is intentionally conceptual. Production SQL generation must replace
plain aggregate expressions with metadata-derived merge expressions when table
metadata reports aggregate-state columns. If `uniq_qs` or p95 values are not
mergeable as exact period metrics, the generated SQL must label their
semantics accordingly.

Conceptual Bot Insights raw fallback template for exact path-grain semantics
when a summary cannot answer the selected v1 path-grain question:

```sql
WITH
  toDateTime('<current_start>') AS current_start,
  toDateTime('<current_end>') AS current_end,
  toDateTime('<baseline_start>') AS baseline_start,
  toDateTime('<baseline_end>') AS baseline_end
SELECT
  request_host,
  request_path_norm,
  bot_class,
  asn_type,
  countIf(timestamp >= current_start AND timestamp < current_end) AS current_requests,
  countIf(timestamp >= baseline_start AND timestamp < baseline_end) AS baseline_requests,
  countIf(timestamp >= current_start AND timestamp < current_end AND cache_was_cached = false) AS current_cache_misses,
  countIf(timestamp >= baseline_start AND timestamp < baseline_end AND cache_was_cached = false) AS baseline_cache_misses,
  uniqExactIf(request_query_string, timestamp >= current_start AND timestamp < current_end) AS current_unique_query_strings,
  uniqExactIf(request_query_string, timestamp >= baseline_start AND timestamp < baseline_end) AS baseline_unique_query_strings,
  quantileIf(0.95)(origin_time_to_first_byte_ms, timestamp >= current_start AND timestamp < current_end AND origin_time_to_first_byte_ms > 0) AS current_origin_p95_ms,
  quantileIf(0.95)(origin_time_to_first_byte_ms, timestamp >= baseline_start AND timestamp < baseline_end AND origin_time_to_first_byte_ms > 0) AS baseline_origin_p95_ms,
  sumIf(response_total_bytes, timestamp >= current_start AND timestamp < current_end) AS current_response_bytes,
  sumIf(response_total_bytes, timestamp >= baseline_start AND timestamp < baseline_end) AS baseline_response_bytes
FROM <project>.bot_detection
WHERE timestamp >= baseline_start
  AND timestamp < current_end
  AND request_host = '<host>'
GROUP BY request_host, request_path_norm, bot_class, asn_type
ORDER BY abs(current_cache_misses - baseline_cache_misses) DESC
LIMIT 50
```

Raw fallback must use a tight timestamp range and must state the fallback
reason, such as `exact_query_string_cardinality_required`. Response-byte
columns from raw fallback should be mapped into `optional_metadata`; they should
not change v1 score or candidate eligibility.

A separate `bot_summary_*` query may be run for host-scope context such as
overall `is_bot_traffic` share or `ai_category` share. The normalizer should put
that context under `optional_metadata.bot_summary_context` and add a limitation
such as `host_scope_context_not_path_level_evidence`.

## Recommended Implementation Shape

V1 should start with the Bot Insights path summary surface because it provides
the clearest cache-busting signal without raw-table scans.

Recommended first script:

```text
skills/bot-insights/scripts/cache_origin_impact.py
```

Responsibilities:

- Parse aggregate JSON, MCP-style `columns`/`rows`, or wrapper input.
- Normalize metric aliases into canonical names.
- Require explicit metric semantics for query-string cardinality, origin
  percentiles, and precomputed contribution fields.
- Compute ratios, deltas, duration-normalized baselines, proxy origin pressure,
  feature points, bands, confidence labels, and limitations.
- Reject mixed row shapes and unsafe inputs.
- Accept high-confidence trusted context only from an in-process reviewed
  wrapper, not from caller-editable JSON.
- Emit `cache_origin_impact_report.v1`.
- Never query Hydrolix or read credentials.

Recommended references:

```text
skills/bot-insights/references/cache-origin-impact.md
```

The Bot Insights reference should document summary-first templates using
`bot_agg_path_*` plus path-grain raw fallback rules. Resource, ASN,
`bot_summary_*`, and SIEM collateral examples should be documented as future
or contextual sections, not v1 detector surfaces.

## Skill-Controlled Boundary

The script's standalone file/stdin mode is useful for reproducibility and
tests, but it cannot prove that JSON came from a reviewed Hydrolix query.

High confidence requires a reviewed skill-controlled wrapper that:

1. Inspects Hydrolix table metadata directly.
2. Selects a reviewed template and dimensions.
3. Generates metadata-correct SQL.
4. Executes through the Hydrolix MCP tool.
5. Receives the MCP result object directly in memory.
6. Maps result rows to the normalizer input.
7. Computes a digest over the mapped payload.
8. Constructs internal trusted context.
9. Calls the normalizer in-process.

This boundary is a workflow guard, not cryptographic proof. It prevents
ordinary saved or pasted JSON from self-attesting as high confidence.

## Integration With Existing Scorecards

`skills/bot-insights/scripts/scorecard.py` already has domains for
`origin_impact` and `cache_busting`. The detector should interoperate with that
scorecard style, but it should not export scorecard-ready rows until the
scorecard handoff contract is explicit.

V1 output can include:

- `finding_types`
- `features`
- `candidate_score`
- `candidate_band`
- `confidence`
- `confidence_reasons`

Later, after scorecard export hardening, it can optionally emit a
`bot_scorecard_input.v1` packet. That export must distinguish preserved
complete-scope contribution from caller-supplied contribution assertions.

## Testing Plan

Unit tests should cover:

- Ratio math with zero denominators.
- Duration normalization for unequal windows.
- Query-string diversity thresholds.
- Cache miss delta and miss-rate delta.
- Origin pressure proxy calculation.
- Contribution withholding when rowsets are source-limited.
- Confidence caps for standalone JSON.
- Missing optional metrics producing `not_evaluated`.
- Rejection of mixed row shapes.
- MCP `columns`/`rows` mapping.
- Feature scoring and band boundaries.

Fixture tests should include:

- Bot path summary candidate with exact `uniq_qs` semantics.
- Bot path summary candidate with approximate query-string semantics.
- Bot path-grain raw fallback candidate for exact query-string semantics.
- Bot-attributable share with multiple selected bot classes summed before
  division by total path misses.
- Optional `bot_summary_*` host-scope context present and correctly limited.
- Unsupported ASN/resource/CDN inputs rejected with known-limitation output.
- Optional response-byte metadata present and absent.
- Sparse low-volume slice.
- Source-limited rowset where contribution is withheld.
- Missing baseline current-only screening.

Documentation checks should verify that examples do not imply local scripts
query Hydrolix or that the detector proves causality.

## Rollout Plan

### Phase 1: Design And References

- Add this design.
- Add a Bot Insights reference page for cache-origin impact.
- Add SQL template examples with metadata and summary-table caveats.

### Phase 2: Standalone Normalizer

- Add `skills/bot-insights/scripts/cache_origin_impact.py`.
- Add fixture-based unit tests.
- Keep standalone confidence capped at `medium`.

### Phase 3: Summary-First Bot Workflow

- Add Bot Insights examples using `bot_agg_path_day/hour/minute`.
- Add path-grain raw fallback examples for exact query-string semantics and
  optional response-byte metadata.
- Capture resource, ASN, CDN, host-only, bot-owner, and exact-status examples as
  desired future features, not v1 detector examples.

### Phase 4: Trusted Runtime And Scorecard Handoff

- Add a reviewed direct-MCP wrapper if this repository can own that runtime
  code.
- Unlock high confidence only through that wrapper.
- Add explicit scorecard export after preserving complete-scope evidence is
  tested.

## Open Questions

- Which deployed summary columns for `uniq_qs`, `p95_origin_ttfb`, and
  `p99_origin_ttfb` are aggregate states versus finalized numeric bucket
  values in each customer deployment?
- Should `origin_pressure_score` use cache misses, `is_origin_request`, or
  another origin-request indicator when both are available?
- What default volume thresholds are appropriate for low-traffic customers?
- Which bot owner or confidence fields are important enough to justify future
  summary expansion rather than repeated raw fallback?
- Should a later version support lead-lag correlation between query-string
  diversity, miss rate, and origin p95 after the deterministic candidate
  detector ships?
