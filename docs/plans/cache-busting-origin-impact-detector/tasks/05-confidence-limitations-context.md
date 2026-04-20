---
task: Implement confidence, limitations, and optional context handling
priority: high
recommended_model: sonnet
estimated_effort: medium
depends_on:
  - 04-detector-scoring-report
test_command: "uv run python tests/test_skill_scripts.py -q"
---

# Task 05: Implement Confidence, Limitations, And Optional Context Handling

## Before Starting

Read these files:

- `docs/cache-busting-origin-impact-detector-design.md` -- confidence labels,
  confidence reasons, trusted-context boundary, limitations, and optional
  metadata rules.
- `skills/bot-insights/scripts/cache_origin_impact.py` -- report assembly from
  Task 04.
- `tests/test_skill_scripts.py` -- detector tests.

## Context

This task hardens report truthfulness. Confidence must describe provenance and
data quality, not just score strength. Optional response bytes and
`bot_summary_*` context must help analysts without changing v1 candidate
eligibility or implying path-level proof.

## Requirements

1. Implement confidence labels:
   - `high` only when `trusted_context` is passed as an in-process argument and
     proves table metadata, retained dimensions, query/result digest,
     comparable windows, sufficient counts, and complete-scope contribution
     evidence;
   - `medium` for well-formed saved or pasted aggregate rows, summary-backed
     rows without direct-MCP trust, approximate query-string uniqueness,
     approximate latency semantics, or one comparable baseline window;
   - `low` for sparse counts, missing baseline, broad raw fallback, partial
     current buckets, source-limited rowsets, missing dimensions, or material
     source coverage caveats.
2. Add machine-readable confidence reasons from the design where applicable:
   `summary_table_used`, `raw_table_fallback`, `retained_dimensions_fit`,
   `missing_retained_dimension`, `path_summary_used`,
   `query_string_cardinality_exact`, `query_string_cardinality_approximate`,
   `origin_latency_merge_exact`, `origin_latency_worst_bucket`,
   `baseline_duration_normalized`, `current_count_sufficient`,
   `baseline_count_sufficient`, `sparse_counts`,
   `complete_scope_contribution`, `contribution_withheld_source_limited`,
   `partial_current_bucket`, `direct_mcp_trusted_context`, and
   `caller_supplied_json_confidence_cap`.
3. Ensure any JSON field named `trusted_context` is ignored for confidence and
   cannot produce `high` confidence. Only the Python `trusted_context` argument
   may do that.
4. Add report and candidate limitations:
   - `query_string_cardinality_approximate` for summed or approximate unique
     query-string semantics;
   - `response_byte_metadata_not_available` when response bytes are absent;
   - `host_scope_context_not_path_level_evidence` for bot-summary context;
   - `contribution_withheld_source_limited` when contribution fields are absent
     or source-limited;
   - raw fallback and missing-dimension limitations as appropriate.
5. Map optional `current_response_bytes` or `response_bytes` fields into
   `optional_metadata.response_bytes` without affecting score or candidate
   eligibility.
6. Accept optional host-scope `bot_summary_context` in the input payload and
   place it under `optional_metadata.bot_summary_context` with the correct
   limitation. Do not use host-scope context to create path-level candidates or
   finding types.
7. Include `origin_pressure_score_is_proxy` and
   `not_a_billing_or_capacity_unit` in interpretation constraints whenever
   origin pressure is evaluated.
8. Add unit tests for confidence caps, trusted in-process high-confidence path,
   sparse low confidence, approximate cardinality, optional response bytes
   present and absent, bot-summary context limitation, and partial current
   bucket/source-limited reasons.

## Acceptance Criteria

- [ ] Standalone JSON output never exceeds `medium` confidence.
- [ ] In-process trusted context can produce `high` confidence only when all
      required proof fields are present.
- [ ] Optional context appears under `optional_metadata` and does not change
      score.
- [ ] Limitations are explicit and machine-readable.
- [ ] `uv run python tests/test_skill_scripts.py -q` passes.

## Related Files

- `skills/bot-insights/scripts/cache_origin_impact.py` -- Modify.
- `tests/test_skill_scripts.py` -- Modify.
