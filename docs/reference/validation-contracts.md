# Validation Contracts

Validation commands should be deterministic, non-interactive, and scoped to the
files or behavior a task changes. Prefer commands that fail for the regression
the task is meant to prevent.

## Repository Commands

The canonical repository checks are:

```bash
uv run python -m unittest discover -s tests
uv run python scripts/validate-skill-examples.py .
```

`scripts/validate-skill-examples.py` may report warnings for existing examples.
Warnings are not silent passes: final reports should identify any warnings that
remain and distinguish pre-existing warnings from warnings introduced by the
current change.

## Input-Contract Changes

When a change touches input grammar, selectors, IDs, pointers, or CLI/wrapper
option resolution, add targeted tests for malformed inputs as well as accepted
inputs. At minimum, cover:

- wrong JSON types for fields that drive behavior, such as selector strings,
  report type strings, labels, arrays, and objects;
- empty or whitespace-only values for identifiers and required text fields;
- duplicate or ambiguous selectors;
- malformed and unresolved pointer or path syntax;
- boundary values, including zero, negative, non-integer, missing, and
  out-of-range values where numeric input is accepted.

These tests should assert the specific fail-closed error class or diagnostic,
not just that rendering eventually fails.

## Documentation Alignment

When implementation decisions change contract language, run targeted searches
before handoff and update the maintained references in the same change. Useful
queries include terms around the changed contract, for example:

```bash
rg -n 'negative|normalize|normaliz|RFC 6901|json_pointer|artifact_id|report_type' docs skills
```

For markdown edits that include JSON, YAML, SQL, shell, or code examples,
inspect the changed fenced blocks and keep complete examples syntactically
valid. Label intentionally partial examples as fragments.

## Review-To-Fix Tracking

For review-to-fix work, keep a short remaining-concerns checklist as findings
are resolved. After each finding class:

- make the smallest scoped fix;
- run the targeted test or query that would have caught the issue;
- update the remaining-concerns list before moving to the next item.

Before saying no issues remain, summarize the validation checks and any
remaining acceptable hits.

## Ralph Plan Validation

A Ralph package is structurally valid only when:

- `tasks/IMPLEMENTATION_PLAN.md` contains parseable checklist entries.
- Each checklist entry references a task file path in backticks.
- Each referenced task file exists.
- Each numbered task file has YAML frontmatter with `test_command`.
- `depends_on` values refer to earlier task files or are empty.
- `tasks/IMPLEMENTATION_PLAN.md` is not treated as a task file during
  frontmatter validation.

Until a dedicated Ralph validator exists, use a small targeted script or shell
check to prove those rules and report the exact command used.

## Warning Policy

Do not hide validator warnings. For warnings outside the requested change:

- leave the underlying files untouched unless the user asked for cleanup;
- state the warning path and message in the handoff;
- avoid broad edits that turn a focused process change into unrelated content
  maintenance.
