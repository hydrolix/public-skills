# Validation Contracts

This page records the repository's maintained validation expectations. Use it
before writing implementation plans, task `test_command` fields, or final
verification summaries.

Validation commands should be deterministic, non-interactive, and scoped to the
files or behavior a task changes. Prefer commands that fail for the regression
the task is meant to prevent.

## Repository Commands

Use `uv run python` in this repository. Bare `python` and `python3` are not
canonical command forms for this user's work.

The canonical repository checks are:

```bash
uv run python -m unittest discover -s tests
uv run python scripts/validate-skill-examples.py .
```

For focused script changes, a narrower test command is acceptable when it covers
the behavior under review:

```bash
uv run python -m unittest tests/test_skill_scripts.py
```

`scripts/validate-skill-examples.py` may report warnings for existing examples.
Warnings are not silent passes: final reports should identify any warnings that
remain and distinguish pre-existing warnings from warnings introduced by the
current change.

## Strict Mode

`scripts/validate-skill-examples.py --strict` treats warnings as failures. Do
not put strict mode in a Ralph task `test_command` unless the current checkout
has zero baseline warnings.

## Ralph Task Commands

Use deterministic, non-interactive commands in task frontmatter. Prefer the
narrowest command that catches the requested change:

- Markdown/reference-only task: `uv run python scripts/validate-skill-examples.py .`
- Script/test task: `uv run python -m unittest tests/test_skill_scripts.py`
- Final docs plus script task:
  `uv run python scripts/validate-skill-examples.py . && uv run python -m unittest tests/test_skill_scripts.py`

Before writing a new task command, run it once in the current checkout or
explain why it cannot be run yet.

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

## Ralph Plan Shape

Use `docs/plans/<slug>/plan.md` as the single source of truth for Ralph task
status. That file must contain the runner-visible checklist with task file paths
relative to `plan.md`, such as `tasks/01-first-task.md`.

Task files live under `docs/plans/<slug>/tasks/` and must start with YAML
frontmatter containing `test_command`. Use task file stems, such as
`01-first-task`, in `depends_on`; do not include the `.md` suffix. Do not add a
mirrored `tasks/IMPLEMENTATION_PLAN.md`.

Until a dedicated Ralph validator exists, use a small targeted script or shell
check to prove those rules and report the exact command used.

## Warning Policy

Do not hide validator warnings. For warnings outside the requested change:

- leave the underlying files untouched unless the user asked for cleanup;
- state the warning path and message in the handoff;
- avoid broad edits that turn a focused process change into unrelated content
  maintenance.

## Final Verification Summaries

Report warning counts separately from errors. If a command passes with warnings,
name the warning and state whether it pre-existing or introduced by the current
change.
