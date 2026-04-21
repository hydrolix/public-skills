---
task: Enforce normalized artifact selection and compatibility rules
priority: high
recommended_model: sonnet
estimated_effort: large
depends_on: ["03-renderer-input-options-scope"]
test_command: "uv run python -m unittest discover -s tests -k render_report"
---

# Task 04: Enforce Normalized Artifact Selection And Compatibility Rules

## Before Starting

Read these files:

- `docs/bot-insights-reporting-design.md` -- artifact normalization and cross-artifact compatibility sections.
- `skills/bot-insights/scripts/render_report.py` -- normalization, selection, and compatibility helpers.
- `tests/test_skill_scripts.py` -- existing packet and compatibility tests.

## Context

Reports must never silently combine artifacts from different scopes, windows,
tables, or entity keys. `bot_scorecard_artifacts.v1` is a packet container; it
satisfies report requirements only through valid normalized child artifacts.

## Requirements

1. Normalize `bot_scorecard_artifacts.v1` into:
   - parent packet artifact;
   - child `<parent>#index` when nested `index` is a valid
     `bot_scorecard_index.v1`;
   - child `<parent>#scorecard-N` for each valid nested
     `bot_entity_scorecard.v1`.
2. Preserve parent artifact ID and parent JSON Pointer on generated children.
3. Enforce artifact ID rules:
   - explicit `artifact_id` values must be non-empty strings;
   - duplicate wrapper `artifact_id` fails;
   - explicit IDs using `#index` or `#scorecard-N` fail;
   - normalized ID collisions fail;
   - exact duplicate bodies without explicit IDs are deduplicated only when
     dedupe cannot affect report selection, ranking, or citations;
   - duplicates with explicit IDs, analyst-note references, or report-selection
     impact fail.
4. Enforce single-primary selection for report types that require one primary
   artifact, with ambiguity failures when multiple candidates exist.
5. Implement scorecard index-to-scorecard pairing:
   - entity type and entity key must match;
   - same-packet children may render after entity match when shared metadata is
     unknown, with degraded warnings;
   - standalone and cross-packet pairings require known matching `scope`,
     `current_window`, `baseline_windows`, and `table_used`;
   - `comparison_type`, when known on either standalone side, must be known on
     both sides and match.
6. Enforce optional artifact compatibility for posture, mover, and control
   companions before combined sections imply shared scope or window.
7. Add tests for same-packet degraded compatibility, standalone compatibility,
   cross-packet failures, duplicate handling, and ambiguous primary artifacts.

## Acceptance Criteria

- [ ] Scorecard packets satisfy reports only through valid normalized children.
- [ ] Required artifact relationships fail closed when compatibility cannot be
  proven.
- [ ] Same-packet missing metadata remains a warning, not a hard failure, after
  entity keys match.
- [ ] Optional incompatible companions are omitted from combined sections with
  visible warnings.
- [ ] `test_command` passes.

## Related Files

- `skills/bot-insights/scripts/render_report.py` -- Modify.
- `tests/test_skill_scripts.py` -- Modify.
