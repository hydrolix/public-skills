# Cache-Busting Origin-Impact Detector Plan Evaluation v1

## Verdict

Not ready for autonomous execution.

The package has a coherent task sequence and the current checkout's baseline
validation commands pass, but the installed Ralph runner discovers zero tasks
because `plan.md` does not contain the runner-visible checklist. Fix the High
finding before execution. Re-review after applying the fixes below.

## Findings

### High: `plan.md` is missing the runner-visible task checklist

**Evidence:** `docs/plans/cache-busting-origin-impact-detector/plan.md` only
points to `tasks/IMPLEMENTATION_PLAN.md` under "Task Package" and contains no
`- [ ] **Task NN** ...` checklist lines. Running `ralph-loop status -v` from the
worktree reports:

```text
cache-busting-origin-impact-detector: 0/0 tasks (0%)
File: docs/plans/cache-busting-origin-impact-detector/plan.md
```

The user-level Ralph format requires the installed runner to parse tasks from
`docs/plans/<slug>/plan.md`, with each task path relative to `plan.md`.

**Impact:** The package is invisible to the runner as executable work. An
executor using the standard Ralph workflow will think there are no tasks and
will not implement the detector.

**Actionable fix:** Add the seven checklist lines to `plan.md`, using paths
relative to `plan.md`, for example
`tasks/01-cache-origin-reference.md`. Keep the checklist in
`tasks/IMPLEMENTATION_PLAN.md` synchronized, and add an explicit note that both
checkbox lists must be updated together during execution.

### Medium: Dimension-set requirements conflict with the design's input shape

**Evidence:** Task 02 defines supported dimension sets as always including
`request_host`, such as `request_host + request_path_norm + bot_class`. The
source design's recommended input shape instead puts the host in
`scope.request_host` and uses `dimensions: ["request_path_norm", "bot_class"]`;
Task 04 also says to include `request_host` in each candidate entity only when
the selected dimension set includes it.

**Impact:** A fresh implementer could encode validation that rejects the
design's recommended payload, or could require every aggregate row to repeat the
host even when the report is already scoped to one host. That would break the
standalone input contract and make the Task 06 end-to-end fixture ambiguous.

**Actionable fix:** Amend the plan/tasks to define the accepted forms
explicitly:

- host scoped once in `scope.request_host`, with row dimensions such as
  `request_path_norm`, `request_path_norm + bot_class`,
  `request_path_norm + asn_type`, or
  `request_path_norm + bot_class + asn_type`;
- or host included in each row as `request_host` when the report is not
  single-host scoped.

Then require tests for both a scoped-host payload and a row-level
`request_host` payload, plus rejection when neither form supplies host scope.

### Medium: CLI verification does not require both supported input paths

**Evidence:** Task 06 requires CLI behavior for positional JSON or stdin,
`-f/--file`, `--limit`, and pretty JSON output, but only asks for "a CLI smoke
test" via `subprocess.run()` or direct `main()` invocation. The acceptance
criteria say stdin or `--file` must emit valid
`cache_origin_impact_report.v1` JSON, but the test requirement does not force
both paths to be exercised.

**Impact:** The implementation can pass the task's test command while one of
the documented CLI input surfaces is broken. This is a user-facing behavior and
should be covered before the task is marked complete.

**Actionable fix:** Update Task 06 to require separate tests for stdin,
`--file`, positional JSON text if supported, `--limit`, and invalid input exit
code/stderr behavior. Keep `python3 tests/test_skill_scripts.py -q` as the
task command.

## Validation Performed

- `git status --short`
- `git branch --show-current`
- `git rev-parse --git-dir`
- `git worktree list --porcelain`
- Read `ralph-loop` skill instructions and `references/plan-format.md`
- Read repo workflow/validation docs, plan, implementation checklist, all seven
  task files, and referenced design/source files needed to trace dependencies
- `ralph-loop status -v` - package discovered, but `0/0 tasks`
- `python3 scripts/validate-skill-examples.py` - passed with the documented
  pre-existing `SELECT *` warning in
  `skills/bot-insights/references/scorecard-analysis.md:119`
- `python3 tests/test_skill_scripts.py -q` - passed, 26 tests
- Scanned plan package for leaked patch markers; none found

## Readiness Notes

All task files have YAML frontmatter with `test_command`, all referenced task
files exist, and the task dependency order is linear. After fixing the runner
checklist and tightening the two Medium issues above, run `ralph-loop status -v`
again and confirm it reports seven tasks before executing the package.
