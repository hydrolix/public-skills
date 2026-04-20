# Cache-Busting Origin-Impact Detector Plan Evaluation v3

## Verdict

Not ready for highest-confidence autonomous execution.

The package is structurally executable: `plan.md` is runner-visible, the
installed runner discovers seven tasks, dependencies are linear, and task files
have deterministic verification commands. However, this review focuses on
whether the task package maximizes the chance of implementing the already
reviewed design correctly. On that axis, two Medium gaps remain. A fresh
implementer could follow the current task text and still miss design-required
input validation or detector eligibility behavior.

## Findings

### Medium: Input validation task omits design-required contract checks

**Evidence:** The design's local input contract lists minimum useful top-level
inputs, including `metric` or `analysis_type`, `dimensions`, `current_window`,
optional `baseline_windows`, `metric_semantics` for semantics-sensitive fields,
and `rows`. It also requires the normalizer to reject ambiguous metric aliases.
Task 02 validates unsupported dimensions, missing rows, host context, mixed row
shapes, missing dimension values, negative counts, non-numeric values,
impossible percentages, and missing `metric_semantics`, but it does not require
validation or tests for missing `metric`/`analysis_type`, missing
`current_window`, or conflicting aliases such as `cnt_all` and `requests` with
different values.

**Impact:** The implementation can pass Task 02 while accepting malformed or
ambiguous aggregate input. That weakens downstream metric derivation because the
script may silently pick one alias, fabricate report metadata defaults, or fail
later with less actionable errors.

**Actionable fix:** Amend Task 02 to require validation and unit tests for:

- missing `metric` or `analysis_type`;
- missing or malformed `current_window`;
- missing or unsupported `dimensions`;
- ambiguous canonical aliases with conflicting values, such as `cnt_all` versus
  `requests`, `cnt_cache_miss` versus `cache_misses`, and percentile aliases.

### Medium: Detector guards and scoring conditions are not self-contained enough

**Evidence:** The design defines exact detector eligibility guards and scoring
conditions: query-string candidates require `current_requests >= 1000`,
`current_unique_query_strings >= 100`, and `qs_diversity_ratio >= 0.5`; cache
miss candidates require `current_requests >= 1000` and
`current_cache_misses >= 100`; origin pressure candidates require
`current_cache_misses >= 100` and `origin_p95_ms > 0`; bot-attributable
candidates require `current_cache_misses >= 100` plus bot share at or above 25%.
The design also defines exact feature conditions for the point score. Task 04
requires "default detector guards" and lists feature names with point values, but
it does not enumerate the guard thresholds or scoring predicates in the task
body. Its required tests mention scoring thresholds and bands, but not per-family
guard acceptance/rejection.

**Impact:** An implementer could produce plausible scoring output while applying
the wrong candidate guards, emitting candidates for sparse rows, or attaching
feature points under the wrong predicates. Those defects are central to whether
the detector implements the reviewed design.

**Actionable fix:** Amend Task 04 to inline the guard thresholds and scoring
predicate table from the design. Require tests for each detector family covering
both just-below and just-at-threshold rows, including sparse current volume,
missing origin latency, absent bot share, and the high-miss-rate threshold of
`miss_rate_pct >= 80`.

### Low: JSON example validity in the final docs task is not machine-checked

**Evidence:** Task 07 requires complete JSON examples to be valid and partial
examples to be labeled as fragments. Its `test_command` runs the repository
skill example validator and script tests, but `scripts/validate-skill-examples.py`
does not parse JSON fenced blocks.

**Impact:** A malformed complete JSON example could pass the task command unless
the executor performs the repo instruction to inspect changed structured
examples manually.

**Actionable fix:** Add an explicit Task 07 acceptance criterion requiring
changed JSON fenced blocks to be inspected, or extend the validator in a
separate task if machine-checked JSON examples become a durable repo
requirement.

## What Is Already Strong

- The plan maps the major design areas into a sensible sequence: reference
  documentation, input normalization, metric derivation, scoring/report assembly,
  confidence/limitations, CLI/e2e tests, and final docs.
- Prior v1 issues are addressed: runner discovery works, host scoping is
  explicit, and CLI coverage now includes stdin, `--file`, positional JSON text,
  `--limit`, and invalid input behavior.
- The task package keeps local scripts away from Hydrolix clients and
  credentials, matching the design boundary.
- Confidence capping, trusted in-process context, optional response bytes,
  host-scope bot-summary context, source-limited contribution withholding, and
  no-causal-claim limitations are represented in task requirements and tests.

## Validation Performed

- `git status --short`
- `git branch --show-current`
- `git rev-parse --git-dir`
- `git worktree list --porcelain`
- `ralph-loop status -v` - package discovered with seven pending tasks
- Reviewed the source design against all seven task files for implementation
  fidelity, not design quality
- `uv run python scripts/validate-skill-examples.py` - passed with the documented
  pre-existing `SELECT *` warning in
  `skills/bot-insights/references/scorecard-analysis.md:119`
- `uv run python tests/test_skill_scripts.py -q` - passed, 26 tests
- Scanned the plan package for leaked patch markers; none found

## Readiness Notes

`plan-evaluation-v2.md` is superseded by this implementation-fidelity review.
Fix the two Medium findings before treating the plan as the highest-chance path
for correct implementation of the reviewed design.
