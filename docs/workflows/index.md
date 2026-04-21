# Workflow Index

This repository uses lightweight, explicit validation commands so agents and
humans can reproduce checks without rediscovering the local Python entrypoint.

## Canonical Validation

Use `uv` for Python-based repository validation:

```bash
uv run python -m unittest discover -s tests
uv run python scripts/validate-skill-examples.py .
```

Avoid raw `python` or `python3` in committed workflow docs and Ralph task
`test_command` values unless a task is documenting a host that does not provide
`uv`.

## Ralph Loop Plan Checks

When creating or reviewing a Ralph Loop package:

1. Run the baseline repository validation commands above.
2. Check that every checklist entry in `tasks/IMPLEMENTATION_PLAN.md` points to
   an existing numbered task file.
3. Exclude `tasks/IMPLEMENTATION_PLAN.md` itself from task-frontmatter checks;
   it is the checklist, not a task.
4. Confirm every numbered task file starts with YAML frontmatter and includes a
   `test_command`.
5. Run each distinct task `test_command` pattern when feasible, or explain why a
   command was not run.

## Existing Validator Warnings

If a validator reports pre-existing warnings outside the touched files, report
them in the final handoff instead of fixing them opportunistically. Fix warning
noise only when it is in scope for the requested work, affects files you
changed, or blocks the requested validation.

## Review-To-Fix Iteration

When turning review findings into fixes, keep a small remaining-concerns list
and update it after each finding class is resolved. Run the targeted regression
check for that finding before moving on, then run the canonical validation when
the batch is complete.
