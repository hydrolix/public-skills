---
task: Finalize skill docs and examples
priority: medium
recommended_model: sonnet
estimated_effort: small
depends_on:
  - 06-cli-and-e2e-tests
test_command: "uv run python scripts/validate-skill-examples.py && uv run python tests/test_skill_scripts.py -q"
---

# Task 07: Finalize Skill Docs And Examples

## Before Starting

Read these files:

- `skills/bot-insights/SKILL.md` -- reference map, query guardrails, and script
  list.
- `skills/bot-insights/references/cache-origin-impact.md` -- detector reference
  created in Task 01.
- `skills/bot-insights/scripts/cache_origin_impact.py` -- final CLI behavior
  from Task 06.

## Context

This task makes the completed detector discoverable from the skill entrypoint
and ensures examples remain valid and honest about the local-script boundary.

## Requirements

1. Update `skills/bot-insights/SKILL.md` to list
   `scripts/cache_origin_impact.py` in Query Guardrails, Reference Map, and
   Script List where appropriate.
2. Update `skills/bot-insights/references/cache-origin-impact.md` with a
   complete standalone input example and a shortened output example. Keep JSON
   examples valid when they are complete; label any partial examples as
   fragments.
3. Make sure docs do not imply local scripts query Hydrolix, prove causality,
   or recommend mitigations.
4. Inspect every changed JSON, SQL, YAML, or code fenced block. Parse complete
   JSON examples manually or with a local parser, and clearly label intentionally
   partial JSON examples as fragments.
5. Confirm markdown links and SQL examples pass the repository validator.

## Acceptance Criteria

- [ ] Bot Insights skill docs point users to the new script and reference.
- [ ] Complete JSON examples are valid and partial examples are labeled as
      fragments.
- [ ] Changed structured fenced blocks were inspected after editing; complete
      JSON examples parse successfully.
- [ ] Documentation preserves the no-local-Hydrolix-query and no-causal-claim
      boundaries.
- [ ] `uv run python scripts/validate-skill-examples.py` passes.
- [ ] `uv run python tests/test_skill_scripts.py -q` passes.

## Related Files

- `skills/bot-insights/SKILL.md` -- Modify.
- `skills/bot-insights/references/cache-origin-impact.md` -- Modify.
