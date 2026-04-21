# Bot Insights Reporting Plan Evaluation v1

Status: not ready for execution.

Review date: 2026-04-19

## Summary

The task package is internally coherent and the numbered task files exist with
frontmatter and `test_command` values. It is not ready for Ralph execution yet
because the default `docs/plans/<slug>/` runner surface is incomplete: the
runner-visible checklist is absent from `plan.md`.

## High Findings

### H1: `plan.md` has no runner-visible task checklist

Severity: High

Evidence:

- `docs/plans/bot-insights-reporting/plan.md` only points readers to
  `docs/plans/bot-insights-reporting/tasks/IMPLEMENTATION_PLAN.md` under
  "Task Package" and contains no task checklist entries.
- `docs/plans/bot-insights-reporting/tasks/IMPLEMENTATION_PLAN.md` contains the
  eight parseable task entries.
- The Ralph Loop format reference requires `plan.md` to contain the
  runner-visible checklist for the default `docs/plans/<slug>/` layout because
  the installed runner parses tasks from `docs/plans/<slug>/plan.md`.

Impact:

A human can find the portable implementation plan, but the default Ralph runner
will not discover the intended tasks from `plan.md`. Execution automation may
report a zero-task plan or skip the package.

Actionable fix:

Add the eight task checklist entries to `plan.md`, using paths relative to
`plan.md`, for example `tasks/01-control-review-producer-metadata.md`. Keep the
same task ids, descriptions, checkbox states, and ordering as
`tasks/IMPLEMENTATION_PLAN.md`. Add a note that both checklists must be updated
together during execution.

## Medium Findings

### M1: Demo rendering acceptance is not guaranteed by Task 08 validation

Severity: Medium

Evidence:

- Task 08 requires "small demo fixture JSON files or documented inline
  examples" and says tests are updated only if executable examples are
  introduced.
- Task 08 acceptance criteria require "Demo examples render with
  `render_report.py` without Hydrolix access."
- The task `test_command` is full unit discovery plus
  `scripts/validate-skill-examples.py`; the repository validator focuses on
  skill structure, local links, and advisory SQL examples, not rendering
  report JSON examples through `render_report.py`.

Impact:

A future implementer can satisfy the letter of the requirements with inline JSON
examples that pass markdown validation but do not actually render. This leaves a
demo-path regression uncaught at the final handoff.

Actionable fix:

Make Task 08 require executable JSON fixture files for each demo example and add
a focused unit test or explicit `test_command` step that runs
`skills/bot-insights/scripts/render_report.py` against each fixture in Markdown
or HTML mode. If inline examples remain allowed, require a documented extraction
or validation path that proves the complete examples render.

## Checks Performed

- Read `plan.md`, `tasks/IMPLEMENTATION_PLAN.md`, all eight numbered task
  files, `docs/reference/validation-contracts.md`, and the Ralph Loop plan
  format reference.
- Confirmed all referenced source/design/test files exist:
  `docs/bot-insights-reporting-design.md`,
  `skills/bot-insights/scripts/compare_posture.py`,
  `skills/bot-insights/scripts/scorecard.py`,
  `skills/bot-insights/scripts/render_report.py`,
  `skills/bot-insights/references/baseline-comparison.md`,
  `skills/bot-insights/references/scorecard-analysis.md`,
  `skills/bot-insights/SKILL.md`, and `tests/test_skill_scripts.py`.
- Confirmed every numbered task file starts with YAML frontmatter and includes
  `depends_on` and `test_command`.
- Confirmed `tasks/IMPLEMENTATION_PLAN.md` references existing numbered task
  files in order.
- Confirmed Python's unittest discovery supports the planned `-k` filter form
  used by task commands with `uv run python -m unittest discover -h`.
- Scanned generated plan markdown for leaked patch markers with:
  `rg -n '^\*\*\* (Begin Patch|End Patch|Add File|Update File|Delete File|Move to):' docs/plans docs/tasks`.

## Readiness Gate

The plan should be re-reviewed after H1 is fixed. Execution should not start
until there are zero High and Medium findings or the execution owner explicitly
accepts the remaining risk.
