---
task: <Task summary>
priority: medium
recommended_model: sonnet
estimated_effort: medium
depends_on:
  - 01-first-task
test_command: "uv run python scripts/validate-skill-examples.py && uv run python tests/test_skill_scripts.py -q"
---

# Task 02: <Task Title>

## Before Starting

Read these files:

- `<path/to/file>` -- <why it matters>

## Context

Explain what this task completes after Task 01.

## Requirements

1. <specific implementation requirement>
2. <specific documentation or integration requirement>
3. Keep examples structurally valid and clearly label fragments.

## Acceptance Criteria

- [ ] <required behavior exists>
- [ ] `uv run python scripts/validate-skill-examples.py` passes.
- [ ] `uv run python tests/test_skill_scripts.py -q` passes.

## Related Files

- `<path/to/file>` -- Modify.
