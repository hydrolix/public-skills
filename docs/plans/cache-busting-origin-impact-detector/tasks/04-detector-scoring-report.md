---
task: Implement detector scoring and report assembly
priority: high
recommended_model: sonnet
estimated_effort: medium
depends_on:
  - 03-metric-derivation
test_command: "uv run python tests/test_skill_scripts.py -q"
---

# Task 04: Implement Detector Scoring And Report Assembly

## Before Starting

Read these files:

- `docs/cache-busting-origin-impact-detector-design.md` -- detector guards,
  strong-signal thresholds, combined score, bands, and output schema.
- `skills/bot-insights/scripts/cache_origin_impact.py` -- metric derivation from
  Task 03.
- `tests/test_skill_scripts.py` -- existing detector tests.

## Context

This task creates the actual `cache_origin_impact_report.v1` packet and ranks
path-grain candidates with explainable feature points. The report must remain
mechanical evidence for an LLM to summarize, not a causal analysis.

## Requirements

1. Assemble top-level report fields:
   - `schema_version`
   - `analysis_type`
   - `source_skill`
   - `comparison_type`
   - `granularity`
   - `table_used`
   - `summary_table_used`
   - `scope`
   - `current_window`
   - `baseline_windows`
   - `baseline_normalization`
   - `metric_semantics`
   - `candidates`
   - `not_evaluated`
   - `interpretation_constraints`
2. Build each candidate entity from the selected dimension set while preserving
   the host-context boundary:
   - when the input uses `scope.request_host`, keep host at report scope and do
     not repeat it on every candidate entity;
   - when the input uses row-level `request_host`, include the non-empty host on
     each candidate entity;
   - avoid empty dimension keys.
3. Implement default detector guards and finding types:
   - query-string diversity candidate only when `current_requests >= 1000`,
     `current_unique_query_strings >= 100`, and `qs_diversity_ratio >= 0.5`;
   - cache-miss movement candidate only when `current_requests >= 1000` and
     `current_cache_misses >= 100`;
   - origin-impact candidate only when `current_cache_misses >= 100` and
     `origin_p95_ms > 0`;
   - bot-attributable cache misses/origin pressure only when
     `current_cache_misses >= 100` and either `bot_miss_share_pct >= 25` or
     `bot_origin_pressure_share_pct >= 25`.
4. Implement combined score contributions exactly from the design:
   - `high_query_string_diversity` 20 when `qs_diversity_ratio >= 0.8`;
   - `moderate_query_string_diversity` 10 when
     `0.5 <= qs_diversity_ratio < 0.8`;
   - `query_string_diversity_increased` 10 when
     `qs_diversity_delta >= 0.25`;
   - `high_miss_rate` 15 when `miss_rate_pct >= 80`;
   - `miss_rate_increased` 15 when `miss_rate_delta_pp >= 10`;
   - `origin_tail_latency_increased` 15 when
     `origin_p95_delta_ms >= 100` and `origin_p95_pct_change >= 50`;
   - `origin_pressure_contributor` 15 when
     `origin_pressure_contribution_pct >= 10`;
   - `bot_attributable_majority` 10 when `bot_miss_share_pct >= 50` or
     `bot_origin_pressure_share_pct >= 50`;
   - `large_current_volume` 5 when `current_requests >= 10000`.
5. Cap combined score at 100 and assign bands:
   - `high` for score >= 70
   - `medium` for score >= 45 and < 70
   - `low` for score >= 20 and < 45
   - `informational` for score < 20
6. Rank volume-sufficient candidates above sparse low-volume candidates, then
   sort by score and origin/cache deltas. Respect an optional `limit` argument
   or CLI flag after complete-scope calculations are done.
7. Compute or preserve share and contribution fields:
   - `cache_miss_contribution_pct`
   - `origin_pressure_contribution_pct`
   - `bot_miss_share_pct`
   - `bot_origin_pressure_share_pct`
   Do not synthesize contribution percentages unless rowset completeness or
   contribution basis proves complete-scope denominators.
8. Populate `share_denominators` when denominator fields are supplied, including
   selected bot classes and contribution basis metadata.
9. Add `not_evaluated` entries for missing detector families or missing required
   inputs, such as query-string cardinality absent, baseline absent, origin p95
   absent, or bot-class share unavailable.
10. Add unit tests for scoring thresholds and bands, per-family guard
    acceptance/rejection at just-below and just-at-threshold values, ranking
    behavior, source limit contribution withholding, multiple selected bot
    classes, scoped-host and row-host entity output, current-only screening, and
    full report shape. Guard tests must cover sparse current volume, query-string
    cardinality just below 100, `qs_diversity_ratio` just below 0.5, cache misses
    just below 100, missing or zero origin latency, absent bot share, bot share
    just below 25%, and the high-miss-rate scoring threshold of
    `miss_rate_pct >= 80`.

## Acceptance Criteria

- [ ] The report schema is `cache_origin_impact_report.v1`.
- [ ] Feature point contributions are visible and sum to a capped candidate
      score.
- [ ] Candidate finding types reflect only evidence actually present.
- [ ] Detector-family guard thresholds are tested at just-below and
      just-at-threshold values.
- [ ] Feature point predicates match the design's exact scoring conditions.
- [ ] Contribution evidence is withheld when denominator basis is source-limited.
- [ ] Sparse low-volume candidates do not outrank volume-sufficient candidates
      solely on score.
- [ ] `uv run python tests/test_skill_scripts.py -q` passes.

## Related Files

- `skills/bot-insights/scripts/cache_origin_impact.py` -- Modify.
- `tests/test_skill_scripts.py` -- Modify.
