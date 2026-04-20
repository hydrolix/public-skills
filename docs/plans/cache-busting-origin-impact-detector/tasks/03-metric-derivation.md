---
task: Implement metric derivation and baseline normalization
priority: high
recommended_model: sonnet
estimated_effort: medium
depends_on:
  - 02-normalizer-input-validation
test_command: "uv run python tests/test_skill_scripts.py -q"
---

# Task 03: Implement Metric Derivation And Baseline Normalization

## Before Starting

Read these files:

- `docs/cache-busting-origin-impact-detector-design.md` -- metric definitions,
  baseline comparison rules, and input row examples.
- `skills/bot-insights/scripts/cache_origin_impact.py` -- validation foundation
  from Task 02.
- `tests/test_skill_scripts.py` -- tests added in Task 02.

## Context

This task turns validated aggregate rows into canonical current, baseline, and
delta metrics. It should not yet decide final candidate ranking beyond making
the computed metrics available to Task 04.

## Requirements

1. Normalize metric aliases into canonical names:
   - `cnt_all`, `requests` -> `requests`
   - `cnt_cache_miss`, `cache_misses` -> `cache_misses`
   - `uniq_qs`, `unique_query_strings` -> `unique_query_strings`
   - `p95_origin_ttfb`, `origin_p95_ms` -> `origin_p95_ms`
   - `p99_origin_ttfb`, `origin_p99_ms` -> `origin_p99_ms`
   - `response_total_bytes`, `response_bytes` -> `response_bytes`
2. Support both `current_*` and `baseline_*` prefixed fields. Do not require
   every optional metric to be present.
3. Compute:
   - `miss_rate_pct`
   - `qs_diversity_ratio`
   - request, cache-miss, miss-rate, query-string diversity, origin-p95, and
     origin-p99 deltas when inputs exist;
   - `cache_miss_pct_change` using the existing repository formula
     `(current - baseline) / max(baseline, 1) * 100`;
   - `origin_p95_pct_change`;
   - `origin_pressure_score = cache_misses * max(origin_p95_ms, 1) / 1000`;
   - `origin_pressure_delta`.
4. Normalize additive baseline metrics to the current window duration when
   current and baseline windows have unequal durations. Do not duration-normalize
   rates, tail latency values, or unique query-string counts.
5. Record baseline normalization metadata:
   - `none_equal_duration_windows` when durations match;
   - `duration_normalized_additive_metrics` with factor and affected additive
     metrics when they differ;
   - `missing_or_current_only` when no usable baseline window exists.
6. Clamp `qs_diversity_ratio` to `0..1` only when
   `metric_semantics.unique_query_strings == "exact_period_unique"`. For other
   semantics, leave the computed value and mark it approximate for later
   confidence handling.
7. Treat missing optional detector metrics as not-evaluated candidates for later
   report assembly, not as zero-valued evidence.
8. Add focused unit tests for zero denominators, unequal-window duration
   normalization, exact versus approximate query-string ratio behavior, origin
   pressure proxy math, and missing optional metrics.

## Acceptance Criteria

- [ ] Canonical metric names appear in candidate current, baseline, and deltas.
- [ ] Unequal baseline windows normalize additive counts before derived rates.
- [ ] Query-string uniqueness semantics control clamping and confidence inputs.
- [ ] Missing optional metrics are carried as missing evidence, not zero.
- [ ] `uv run python tests/test_skill_scripts.py -q` passes.

## Related Files

- `skills/bot-insights/scripts/cache_origin_impact.py` -- Modify.
- `tests/test_skill_scripts.py` -- Modify.
