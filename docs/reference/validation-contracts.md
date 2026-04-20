# Validation Contracts

This page records the repository's maintained validation expectations. Use it
before writing implementation plans, task `test_command` fields, or final
verification summaries.

## Baseline Commands

Use `uv run python` in this repository. Bare `python` and `python3` are not
canonical command forms for this user's work.

Run the skill example validator after editing skill markdown, references, or SQL
examples:

```bash
uv run python scripts/validate-skill-examples.py
```

Run the current script test suite after editing skill scripts or their tests:

```bash
uv run python tests/test_skill_scripts.py -q
```

`tests/` is not currently a Python package in this checkout, so use the direct
test file invocation above instead of `uv run python -m unittest tests.test_skill_scripts`.

## Strict Mode

`scripts/validate-skill-examples.py --strict` treats warnings as failures. Do
not put strict mode in a Ralph task `test_command` unless the current checkout
has zero baseline warnings.

Known baseline warning as of this workflow note:

- `skills/bot-insights/references/scorecard-analysis.md:119` uses `SELECT *` in
  a SQL example.

The non-strict validator command is the canonical task-level command until that
baseline warning is removed.

## Ralph Task Commands

Use deterministic, non-interactive commands in task frontmatter. Prefer the
narrowest command that catches the requested change:

- Markdown/reference-only task: `uv run python scripts/validate-skill-examples.py`
- Script/test task: `uv run python tests/test_skill_scripts.py -q`
- Final docs plus script task:
  `uv run python scripts/validate-skill-examples.py && uv run python tests/test_skill_scripts.py -q`

Before writing a new task command, run it once in the current checkout or
explain why it cannot be run yet.

## Ralph Plan Shape

Use `docs/plans/<slug>/plan.md` as the single source of truth for Ralph task
status. That file must contain the runner-visible checklist with task file paths
relative to `plan.md`, such as `tasks/01-first-task.md`.

Task files live under `docs/plans/<slug>/tasks/` and must start with YAML
frontmatter containing `test_command`. Use task file stems, such as
`01-first-task`, in `depends_on`; do not include the `.md` suffix. Do not add a
mirrored `tasks/IMPLEMENTATION_PLAN.md`.

## Final Verification Summaries

Report warning counts separately from errors. If a command passes with warnings,
name the warning and state whether it pre-existing or introduced by the current
change.
