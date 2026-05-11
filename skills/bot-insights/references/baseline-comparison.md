# bot-insights — Baseline Comparison

Bot Insights baseline analytics are for posture movement, program health, and
control review. They are not a replacement for real-time mitigation consoles.

## Contents

- [Hydrolix Boundary](#hydrolix-boundary)
- [First-Class Methods](#first-class-methods)
- [Confidence](#confidence)
- [Output Schemas](#output-schemas)
- [SQL Templates](#sql-templates)

Queries should produce aggregate current/baseline rows from Hydrolix summaries;
local scripts may then compute deterministic deltas and structured outputs.

## Hydrolix Boundary

Use Hydrolix for the work it is best at:

- filtering by timestamp, host, path, ASN, policy, action, and retained summary
  dimensions;
- aggregating large row sets from summary tables;
- computing rates that require summary columns, merge functions, or table-local
  SQL semantics;
- returning small current/baseline or before/after aggregate result sets.

Use `scripts/compare_posture.py` only after Hydrolix has produced those
aggregate rows. Its value over plain SQL is not raw compute power; it is a
stable product-facing packet:

- one schema for posture, mover attribution, and control review;
- consistent delta formula, direction labels, contribution percentage, and
  control status labels across query templates;
- confidence labels with machine-readable reasons;
- explicit interpretation constraints that keep the LLM from treating movement
  as causality or doing analysis over raw records;
- offline reproducibility from saved MCP results, pasted JSON, or CI fixtures.

Shared deterministic baseline semantics live in `scripts/baselines.py`. Keep
delta math, direction labels, numeric sanitization, count support checks,
granularity matching, and confidence labels there when multiple scripts need
the same behavior. Keep `scripts/compare_posture.py` as the product-facing CLI
for posture, simple mover, and control-review packets.

If a one-off SQL query already answers the question and no structured packet,
confidence metadata, or repeatable handoff is needed, do not add script work.
Keep the result in Hydrolix.

## First-Class Methods

| Method | Baseline | Default granularity | Use |
|--------|----------|---------------------|-----|
| `quarter_over_quarter` | previous complete quarter | day | executive posture and program movement |
| `month_over_month` | previous complete month | day | monthly health and policy movement |
| `week_over_week` | previous complete week | day or hour | weekly posture and team routing |
| `year_over_year` | same period in previous year | day | seasonal posture movement |
| `same_week_last_year` | same ISO week in previous year | day | retail, launch, or annual-event comparison |
| `same_weekday_hour_last_week` | same weekday and hour one week earlier | hour | weekday/hour seasonality |
| `same_hour_yesterday` | same hour one day earlier | hour | daily rhythm and fresh shifts |
| `previous_window` | immediately preceding equal-length window | hour or minute | short SOC-style comparison |
| `explicit_before_after` | user-provided before/after windows | minute, hour, or day | known change review |
| `post_change_vs_expected` | expected value or expected window after a change | day, hour, or minute | control effectiveness and collateral checks |

Use day summaries for QoQ, MoM, YoY, and executive posture. Use hour summaries
for weekday/hour seasonality and daily rhythm. Use minute summaries only for
short policy-change review or detailed incident follow-up.

## Confidence

Confidence is label-plus-reasons, not an opaque score.

Labels:

- `high`: summary table used, comparable windows available, current and
  baseline counts meet minimums, and granularity matches the comparison type.
- `medium`: summary table used but only one comparable window exists, a
  substitute baseline was selected, or a source coverage caveat applies.
- `low`: sparse counts, partial current bucket, request-level query, missing
  retained dimension, or material source-specific enrichment caveat.

Machine-readable reasons:

- `summary_table_used`
- `request_level_query`
- `retained_dimensions_fit`
- `missing_retained_dimension`
- `comparable_windows_available`
- `substitute_baseline_selected`
- `granularity_matches_comparison`
- `granularity_mismatch`
- `current_count_sufficient`
- `baseline_count_sufficient`
- `sparse_counts`
- `partial_current_bucket`
- `source_coverage_caveat`
- `zero_baseline_guard`

## Output Schemas

### `bot_posture_movement.v1`

```json
{
  "schema_version": "bot_posture_movement.v1",
  "comparison_type": "quarter_over_quarter",
  "granularity": "day",
  "table_used": "bi_summary_day",
  "scope": {"request_host": "www.example.com"},
  "current_window": {"start": "2026-01-01", "end": "2026-04-01"},
  "baseline_windows": [
    {"start": "2025-10-01", "end": "2026-01-01", "label": "previous_quarter"}
  ],
  "metrics": [
    {
      "name": "bot_share_pct",
      "unit": "percent",
      "current": 34.2,
      "baseline": 29.7,
      "absolute_delta": 4.5,
      "pct_change": 15.15,
      "direction": "increase",
      "confidence": "high",
      "confidence_reasons": ["summary_table_used", "baseline_count_sufficient"]
    }
  ],
  "interpretation_constraints": [
    "movement_only",
    "no_causal_claim",
    "llm_may_summarize_structured_evidence_only"
  ]
}
```

### `bot_mover_attribution.v1`

Mover artifacts preserve supplied `scope`, `current_window`, and
`baseline_windows` metadata so renderers can safely pair them with posture,
scorecard, or control-review artifacts from the same comparison context.

```json
{
  "schema_version": "bot_mover_attribution.v1",
  "comparison_type": "month_over_month",
  "granularity": "day",
  "table_used": "bi_summary_day",
  "scope": {"request_host": "www.example.com"},
  "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
  "baseline_windows": [
    {"start": "2026-03-25", "end": "2026-04-01"}
  ],
  "dimension": "client_asn",
  "metric": "requests",
  "total_delta": 120000,
  "total_delta_basis": "complete_scope_total_abs_delta",
  "movers": [
    {
      "value": "12345",
      "current": 64000,
      "baseline": 10000,
      "absolute_delta": 54000,
      "pct_change": 540.0,
      "direction": "increase",
      "contribution_pct": 45.0,
      "confidence": "high",
      "confidence_reasons": ["summary_table_used", "baseline_count_sufficient"]
    }
  ],
  "interpretation_constraints": [
    "attribution_from_aggregate_deltas",
    "no_causal_claim",
    "llm_may_summarize_structured_evidence_only"
  ]
}
```

### `bot_control_review.v1`

Control-review artifacts preserve the windows the caller supplied so the report
can show what "before", "after", and "expected" mean without inferring them
from `change_time`:

- `before_window` and `after_window` are preserved when supplied; they remain
  absent otherwise (never inferred).
- `expected_window` is preserved when the expected values came from a time
  window. When the producer uses `before_window` as the expected baseline, it
  copies that supplied window directly into `expected_window`; omit it when
  expected values did not come from a window.
- `scope` is preserved when supplied.
- `expected_basis` records where the expected values came from. Valid values:
  - `before_window`: the producer used the before period as the expected
    baseline (emitted automatically when `expected` is not supplied and
    `before` is present);
  - `explicit_target`: the caller supplied literal expected values;
  - `external_model`: the caller supplied expected values from another model
    or forecast (only emitted when the input explicitly states this basis);
  - `unknown`: the producer could not determine the basis.

An explicitly supplied valid `expected_basis` on the input is always
preserved verbatim.

```json
{
  "schema_version": "bot_control_review.v1",
  "comparison_type": "post_change_vs_expected",
  "change_time": "2026-04-01T00:00:00Z",
  "target": {"policy_id": "policy-123"},
  "scope": {"request_host": "www.example.com"},
  "before_window": {"start": "2026-03-25T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
  "after_window": {"start": "2026-04-01T00:00:00Z", "end": "2026-04-08T00:00:00Z"},
  "expected_basis": "before_window",
  "expected_window": {
    "start": "2026-03-25T00:00:00Z",
    "end": "2026-04-01T00:00:00Z"
  },
  "table_used": "bi_siem_policy_summary_day",
  "target_effects": [
    {
      "metric": "siem_blocked_requests",
      "before": 880000,
      "after": 1200000,
      "expected": 910000,
      "absolute_delta_vs_expected": 290000,
      "pct_change_vs_expected": 31.87,
      "direction": "increase",
      "status": "increased",
      "confidence": "high",
      "confidence_reasons": ["summary_table_used", "baseline_count_sufficient"]
    }
  ],
  "collateral_checks": [],
  "displacement_checks": [],
  "interpretation_constraints": [
    "control_effectiveness_review",
    "no_causal_claim_without_external_change_evidence",
    "llm_may_summarize_structured_evidence_only"
  ]
}
```

## SQL Templates

These templates produce aggregate rows for the local posture comparison script.
They intentionally do not include client setup, credentials, or execution logic.

Replace `<posture_summary_day>` with `bi_summary_day`. Replace
`<siem_summary_day>` with `bi_siem_policy_summary_day` on TrafficPeak/Akamai.

If the Hydrolix metadata reports aggregate-state columns, replace `sum(metric)`
with the reported merge function.

### Period Rows from a Daily Summary

```sql
WITH
  toDateTime('<current_start>') AS current_start,
  toDateTime('<current_end>') AS current_end,
  toDateTime('<baseline_start>') AS baseline_start,
  toDateTime('<baseline_end>') AS baseline_end
SELECT
  period,
  sum(cnt_all) AS requests,
  round(sumIf(cnt_all, is_bot_traffic = true) / greatest(sum(cnt_all), 1) * 100, 2) AS bot_share_pct,
  round(sumIf(cnt_all, userAgentCategory = 'Search Engine Crawler') / greatest(sum(cnt_all), 1) * 100, 2) AS good_bot_share_pct,
  round(sum(cnt_429) / greatest(sum(cnt_all), 1) * 100, 2) AS rate_429_pct,
  round(sum(cnt_5xx) / greatest(sum(cnt_all), 1) * 100, 2) AS rate_5xx_pct,
  round(sum(cnt_cache_miss) / greatest(sum(cnt_all), 1) * 100, 2) AS cache_miss_pct,
  max(p95_origin_ttfb) AS origin_p95_ms
FROM (
  SELECT 'current' AS period, *
  FROM <project>.<posture_summary_day>
  WHERE timestamp >= current_start
    AND timestamp < current_end
    AND request_host = '<host>'
  UNION ALL
  SELECT 'baseline' AS period, *
  FROM <project>.<posture_summary_day>
  WHERE timestamp >= baseline_start
    AND timestamp < baseline_end
    AND request_host = '<host>'
)
GROUP BY period
ORDER BY period
```

`good_bot_share_pct` filters on `userAgentCategory = 'Search Engine Crawler'`
because deployed posture summaries do not retain a queryable `bot_class`
column; confirm the metadata-matched user-agent category value for the
target cluster. `bad_bot_share_pct` is intentionally omitted: there is no
clean `userAgentCategory` value for "bad". For SIEM-grade bad-bot share on
SIEM-enabled clusters, layer `bi_siem_policy_summary_*` (filter on
`botType`) on top of this template; otherwise apply the
deployment-availability rule (SKILL.md). See the metadata-alias note in
[scorecard-analysis.md](scorecard-analysis.md) for background.

### Mover Attribution from a Summary

```sql
WITH
  toDateTime('<current_start>') AS current_start,
  toDateTime('<current_end>') AS current_end,
  toDateTime('<baseline_start>') AS baseline_start,
  toDateTime('<baseline_end>') AS baseline_end
SELECT
  client_asn AS value,
  sumIf(cnt_all, timestamp >= current_start AND timestamp < current_end) AS current,
  sumIf(cnt_all, timestamp >= baseline_start AND timestamp < baseline_end) AS baseline,
  current - baseline AS absolute_delta,
  round(absolute_delta / greatest(baseline, 1) * 100, 2) AS pct_change
FROM <project>.<posture_summary_day>
WHERE timestamp >= baseline_start
  AND timestamp < current_end
  AND request_host = '<host>'
GROUP BY client_asn
ORDER BY abs(absolute_delta) DESC
LIMIT 20
```

### Control Review from SIEM Summaries

```sql
WITH
  toDateTime('<before_start>') AS before_start,
  toDateTime('<change_time>') AS change_time,
  toDateTime('<after_end>') AS after_end
SELECT
  period,
  countMerge(`count()`) AS requests,
  countIfMerge(`countIf(equals(actionClass, 'deny'))`) AS siem_blocked_requests,
  countIfMerge(`countIf(equals(authOutcome, 'fail'))`) AS siem_auth_fail_requests,
  countMergeIf(`count()`, status >= 500 AND status < 600) AS siem_5xx_requests,
  round(siem_5xx_requests / greatest(requests, 1) * 100, 2) AS rate_5xx_pct
FROM (
  SELECT 'before' AS period, *
  FROM <project>.<siem_summary_day>
  WHERE timestamp >= before_start
    AND timestamp < change_time
    AND policyId = '<policy_id>'
  UNION ALL
  SELECT 'after' AS period, *
  FROM <project>.<siem_summary_day>
  WHERE timestamp >= change_time
    AND timestamp < after_end
    AND policyId = '<policy_id>'
)
GROUP BY period
ORDER BY period
```

The example above matches TrafficPeak/Akamai `bi_siem_policy_summary_*`.
