---
task: Preserve control-review report metadata
priority: high
recommended_model: sonnet
estimated_effort: medium
depends_on: []
test_command: "uv run python -m unittest discover -s tests -k control_review && uv run python scripts/validate-skill-examples.py ."
---

# Task 01: Preserve Control-Review Report Metadata

## Before Starting

Read these files:

- `docs/bot-insights-reporting-design.md` -- accepted report contract, especially Phase 0 and `bot_control_review.v1` metadata.
- `skills/bot-insights/scripts/compare_posture.py` -- control-review artifact producer.
- `skills/bot-insights/references/baseline-comparison.md` -- schema examples and control-review guidance.
- `tests/test_skill_scripts.py` -- current producer and renderer test style.

## Context

Control-review reports must show what "before", "after", and "expected" mean
without inferring windows from `change_time`. The renderer can warn on missing
metadata, but the producer should preserve metadata when callers supply it.

## Requirements

1. Update `compare_posture.py` so `bot_control_review.v1` preserves:
   - `scope`;
   - `before_window`;
   - `after_window`;
   - `expected_window` when expected values came from a time window;
   - `expected_basis` with one of `before_window`, `explicit_target`,
     `external_model`, or `unknown`.
2. Make `expected_basis` deterministic:
   - use an explicitly supplied valid `expected_basis` when present;
   - use `before_window` when the producer uses the before period as the
     expected baseline;
   - use `explicit_target` when the caller supplied literal expected values;
   - use `external_model` only when input metadata states that basis;
   - use `unknown` only when the producer cannot determine the basis.
3. Do not infer missing windows from `change_time`.
4. Update `baseline-comparison.md` examples and text to match the producer
   behavior.
5. Add or update unit tests for every `expected_basis` branch above and for
   preservation of supplied windows and scope.

## Acceptance Criteria

- [ ] `bot_control_review.v1` artifacts preserve supplied window and scope metadata.
- [ ] Missing windows remain absent rather than being inferred.
- [ ] `expected_basis` behavior matches the design.
- [ ] The reference example matches the emitted schema.
- [ ] `test_command` passes.

## Related Files

- `skills/bot-insights/scripts/compare_posture.py` -- Modify.
- `skills/bot-insights/references/baseline-comparison.md` -- Modify.
- `tests/test_skill_scripts.py` -- Modify.
