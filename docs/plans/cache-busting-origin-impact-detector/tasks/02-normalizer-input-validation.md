---
task: Add normalizer input parsing and validation foundation
priority: high
recommended_model: sonnet
estimated_effort: medium
depends_on:
  - 01-cache-origin-reference
test_command: "uv run python tests/test_skill_scripts.py -q"
---

# Task 02: Add Normalizer Input Parsing And Validation Foundation

## Before Starting

Read these files:

- `docs/cache-busting-origin-impact-detector-design.md` -- input contract and
  validation requirements.
- `skills/bot-insights/scripts/compare_posture.py` -- MCP `columns`/`rows`
  mapping and CLI style.
- `skills/bot-insights/scripts/scorecard.py` -- scorecard artifact style,
  confidence reason conventions, and row-shape rejection patterns.
- `tests/test_skill_scripts.py` -- existing script test loader and Bot Insights
  test organization.

## Context

This task creates the new script and enough validation to reject unsafe or
unsupported inputs before any scoring logic exists. It should keep local code
dependency-light and compatible with the repository's single unittest file.

## Requirements

1. Create `skills/bot-insights/scripts/cache_origin_impact.py`.
2. Add constants for:
   - `REPORT_SCHEMA = "cache_origin_impact_report.v1"`
   - `ANALYSIS_TYPE = "cache_busting_origin_impact"`
   - supported row-level path-grain dimension sets:
     - `request_path_norm`
     - `request_path_norm + bot_class`
     - `request_path_norm + asn_type`
     - `request_path_norm + bot_class + asn_type`
   - accepted host context forms:
     - single-host report scope in `scope.request_host`, with rows using one of
       the row-level dimension sets above;
     - row-level `request_host` included with every row when the report is not
       scoped to one host.
   - interpretation constraints from the design.
3. Implement `column_names()` and `result_rows()` compatible with existing
   script behavior: dictionary rows pass through, MCP-style `columns` plus list
   `rows` convert to dictionaries, and invalid row containers return an empty
   list or raise a clear validation error at the public entrypoint.
4. Implement `build_report(value, trusted_context=None, *, limit=None)` as the
   public in-process API. It may emit an empty candidate list until later tasks,
   but it must validate the input contract.
5. Implement validation for:
   - missing `metric` or `analysis_type`;
   - missing or malformed `current_window`;
   - missing, empty, or unsupported `dimensions`;
   - unsupported non-path-grain dimensions;
   - missing required `rows`;
   - missing host context when neither `scope.request_host` nor row-level
     `request_host` is supplied;
   - mixed period-split and combined row shapes;
   - missing dimension values on supported dimensions;
   - ambiguous canonical metric aliases with conflicting values, such as
     `cnt_all` versus `requests`, `cnt_cache_miss` versus `cache_misses`,
     `uniq_qs` versus `unique_query_strings`, and percentile aliases;
   - negative counts;
   - non-numeric numeric fields;
   - impossible precomputed percentages outside `0..100`;
   - missing `metric_semantics` when rows include query-string cardinality,
     origin percentile, or precomputed contribution fields.
6. Ignore any caller-supplied JSON field named `trusted_context` for confidence
   purposes and record a reason that later tasks can surface.
7. Add the script to `BotInsightsScriptTests.setUpClass` in
   `tests/test_skill_scripts.py`.
8. Add unit tests for MCP row mapping, supported dimension validation,
   scoped-host payloads, row-level `request_host` payloads, missing host-context
   rejection, missing `metric` or `analysis_type`, missing or malformed
   `current_window`, missing or unsupported `dimensions`, unsupported dimension
   rejection, ambiguous alias rejection, mixed row-shape rejection, semantic
   metadata requirement, and standalone `trusted_context` ignoring.

## Acceptance Criteria

- [ ] The new script imports without side effects.
- [ ] `build_report()` accepts object rows and MCP `columns` plus `rows`.
- [ ] Host context is accepted from `scope.request_host` or row-level
      `request_host`, and rejected when absent.
- [ ] Required top-level contract fields are validated with clear messages:
      `metric` or `analysis_type`, `dimensions`, `current_window`, and `rows`.
- [ ] Conflicting metric aliases are rejected instead of silently picking a
      value.
- [ ] Unsupported v1 dimensions such as `client_asn`, `resource_category`, and
      `hdx_cdn` are rejected with a clear message.
- [ ] Caller-supplied JSON trust data cannot enable high-confidence output.
- [ ] `uv run python tests/test_skill_scripts.py -q` passes.

## Related Files

- `skills/bot-insights/scripts/cache_origin_impact.py` -- Create.
- `tests/test_skill_scripts.py` -- Modify.
