---
task: Wire CLI behavior and end-to-end tests
priority: medium
recommended_model: sonnet
estimated_effort: medium
depends_on:
  - 05-confidence-limitations-context
test_command: "uv run python tests/test_skill_scripts.py -q"
---

# Task 06: Wire CLI Behavior And End-To-End Tests

## Before Starting

Read these files:

- `docs/cache-busting-origin-impact-detector-design.md` -- final output schema
  and rollout notes.
- `skills/bot-insights/scripts/cache_origin_impact.py` -- completed normalizer.
- `tests/test_skill_scripts.py` -- existing unit test style.

## Context

This task makes the detector usable from the command line and adds a complete
fixture-like regression test. Documentation updates happen in the next task so
this task stays focused on code and tests.

## Requirements

1. Implement CLI behavior in `cache_origin_impact.py` following existing script
   style:
   - positional JSON text or stdin;
   - `-f/--file`;
   - `--limit`;
   - pretty JSON output by default.
2. Ensure CLI errors print `ERROR: <message>` to stderr and return exit code 1.
3. Add an end-to-end unit test that feeds a representative path-summary payload
   with exact `uniq_qs` semantics, baseline data, contribution denominators,
   selected bot classes, and optional bot-summary context. Assert the output
   includes:
   - schema version;
   - a high-band candidate;
   - expected feature names;
   - contribution basis;
   - confidence cap when called without in-process trusted context;
   - interpretation constraints.
4. Add CLI tests using `subprocess.run()` or direct `main()` invocation,
   consistent with the existing test style, covering:
   - stdin JSON input;
   - `--file` JSON input;
   - positional JSON text input;
   - `--limit` applying after report calculations;
   - invalid input returning exit code 1 and `ERROR: <message>` on stderr.

## Acceptance Criteria

- [ ] Running the script from stdin or `--file` emits valid
      `cache_origin_impact_report.v1` JSON.
- [ ] Positional JSON text input emits valid `cache_origin_impact_report.v1`
      JSON.
- [ ] `--limit` is covered by tests and does not affect complete-scope
      denominator calculations.
- [ ] CLI invalid input returns exit code 1 with a clear error.
- [ ] End-to-end fixture test covers the complete v1 report shape.
- [ ] `uv run python tests/test_skill_scripts.py -q` passes.

## Related Files

- `skills/bot-insights/scripts/cache_origin_impact.py` -- Modify.
- `tests/test_skill_scripts.py` -- Modify.
