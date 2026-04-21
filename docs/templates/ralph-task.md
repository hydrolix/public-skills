---
task: Replace with concise deliverable summary
priority: medium
recommended_model: sonnet
estimated_effort: medium
depends_on: []
test_command: "uv run python -m unittest discover -s tests"
---

# Task NN: Replace With Task Title

## Before Starting

Read these files:

- `path/to/file.py` -- why this file matters.
- `tests/test_file.py` -- relevant existing coverage.

## Context

Explain why this task exists and how it fits with adjacent tasks. Keep this
section short enough for a fresh-context implementer.

## Requirements

1. Modify or create specific files.
2. Define the required behavior, API, schema, or documentation change.
3. Add or update focused tests.

## Acceptance Criteria

- [ ] Required behavior exists.
- [ ] Relevant edge cases are covered.
- [ ] `test_command` passes.

## Related Files

- `path/to/file.py` -- Modify.
- `tests/test_file.py` -- Modify or create.
