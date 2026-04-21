---
task: Add scorecard provenance and limit metadata contracts
priority: high
recommended_model: sonnet
estimated_effort: medium
depends_on: ["01-control-review-producer-metadata"]
test_command: "uv run python -m unittest discover -s tests -k scorecard && uv run python scripts/validate-skill-examples.py ."
---

# Task 02: Add Scorecard Provenance And Limit Metadata Contracts

## Before Starting

Read these files:

- `docs/bot-insights-reporting-design.md` -- Phase 0 scorecard provenance and producer-limit metadata contract.
- `skills/bot-insights/scripts/scorecard.py` -- scorecard and index producer.
- `skills/bot-insights/references/scorecard-analysis.md` -- scorecard artifact documentation.
- `tests/test_skill_scripts.py` -- current scorecard tests.

## Context

Crawler governance must not treat generic 429/5xx rate movement as
crawler-specific evidence unless structured provenance proves the rowset.
Reports also need to disclose producer-side truncation without pretending to
know source population counts.

## Requirements

1. Preserve `rowset_scope` on emitted `bot_entity_scorecard.v1` artifacts when
   callers supply it at payload level or row level.
2. Preserve `feature_provenance` on emitted scorecards when callers supply it.
   Feature-level provenance must override artifact-level provenance during
   renderer interpretation, but this task only needs to preserve the structure.
3. Validate provenance only enough to keep the artifact deterministic:
   - `rowset_scope.population`, when present, must be one of `crawler`,
     `good_bot`, `ai_crawler`, `all_traffic`, or `unknown`;
   - `feature_provenance` must be an object keyed by feature name when present;
   - `feature_provenance.<feature>.metric_inputs`, when present, must be an
     array of strings.
4. Keep existing producer-side limit metadata for metadata-capable outputs:
   - default `bot_scorecard_artifacts.v1`;
   - `--output index`.
5. Confirm bare `--output scorecards` remains a plain list without packet-level
   producer metadata.
6. Update `scorecard-analysis.md` with the provenance and producer-limit
   contract.
7. Add unit tests for provenance preservation, invalid provenance rejection, and
   all three limit-output paths.

## Acceptance Criteria

- [ ] Scorecards preserve valid `rowset_scope` and `feature_provenance`.
- [ ] Invalid provenance shapes fail closed with clear errors.
- [ ] Metadata-capable outputs keep `producer_limit`, `result_row_count`,
  `result_truncated`, and `total_ranked_entities` when `--limit` is applied.
- [ ] Bare scorecard-list output is documented and tested as the emitted known
  collection.
- [ ] `test_command` passes.

## Related Files

- `skills/bot-insights/scripts/scorecard.py` -- Modify.
- `skills/bot-insights/references/scorecard-analysis.md` -- Modify.
- `tests/test_skill_scripts.py` -- Modify.
