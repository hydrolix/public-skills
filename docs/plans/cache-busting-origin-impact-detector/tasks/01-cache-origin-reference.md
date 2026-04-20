---
task: Add Bot Insights cache-origin reference documentation
priority: high
recommended_model: sonnet
estimated_effort: medium
depends_on: []
test_command: "uv run python scripts/validate-skill-examples.py"
---

# Task 01: Add Bot Insights Cache-Origin Reference Documentation

## Before Starting

Read these files:

- `docs/cache-busting-origin-impact-detector-design.md` -- source design and
  acceptance criteria.
- `skills/bot-insights/SKILL.md` -- reference map and script list conventions.
- `skills/bot-insights/references/edge-ops-analysis.md` -- existing cache and
  origin guidance to keep aligned.
- `skills/bot-insights/references/summary-tables.md` -- summary table inventory
  and retained dimensions.
- `scripts/validate-skill-examples.py` -- markdown and SQL example validator.

## Context

The detector needs a durable skill reference before implementation so future
agents know the v1 boundary: path-summary first, local scripts consume aggregate
JSON only, and SQL examples must be metadata-aware rather than executable
copy-paste queries with inferred merge functions.

## Requirements

1. Create `skills/bot-insights/references/cache-origin-impact.md`.
2. Document the v1 scope, non-goals, supported dimension sets, supported
   detector families, input contract, output schema shape, confidence boundary,
   and future non-path-grain detector surfaces.
3. Include SQL template guidance for `bot_agg_path_day/hour/minute` and tight
   raw fallback, but clearly label templates as metadata-aware examples.
4. State that summary table metadata must be inspected before querying and that
   aggregate-state metrics require the metadata-provided merge function.
5. State that local scripts must not query Hydrolix, read credentials, or make
   causal or mitigation claims.
6. Update `skills/bot-insights/SKILL.md` so cache busting, query-string churn,
   and origin-impact requests point to the new reference first, while
   `edge-ops-analysis.md` remains a broader analysis reference.
7. Update `skills/bot-insights/references/edge-ops-analysis.md` to point users
   to the new detector reference for structured `cache_origin_impact_report.v1`
   output. Keep existing examples valid under the repository validator.

## Acceptance Criteria

- [ ] `cache-origin-impact.md` exists and is linked from `SKILL.md`.
- [ ] Documentation preserves the v1 path-grain-only detector boundary.
- [ ] SQL examples mention metadata inspection and do not imply merge functions
      can be inferred from column names.
- [ ] Documentation states standalone scripts accept aggregate rows only and do
      not query Hydrolix.
- [ ] `uv run python scripts/validate-skill-examples.py` passes.

## Related Files

- `skills/bot-insights/references/cache-origin-impact.md` -- Create.
- `skills/bot-insights/SKILL.md` -- Modify.
- `skills/bot-insights/references/edge-ops-analysis.md` -- Modify.
