---
task: Harden renderer input grammar, option resolution, and scope display
priority: high
recommended_model: sonnet
estimated_effort: medium
depends_on: ["02-scorecard-provenance-and-limits"]
test_command: "uv run python -m unittest discover -s tests -k render_report"
---

# Task 03: Harden Renderer Input Grammar, Options, And Scope

## Before Starting

Read these files:

- `docs/bot-insights-reporting-design.md` -- accepted input grammar, wrapper, option resolution, and scope rules.
- `skills/bot-insights/scripts/render_report.py` -- renderer implementation.
- `tests/test_skill_scripts.py` -- existing renderer tests.

## Context

The renderer is the deterministic view layer. It must resolve intent and
presentation options before validating required artifacts, and it must display
scope without using wrapper presentation text as compatibility evidence.

## Requirements

1. Enforce the accepted top-level input grammar:
   - single known artifact object;
   - non-empty raw artifact array;
   - `bot_report_input.v1` wrapper.
2. Reject unsupported top-level shapes, empty arrays, non-object artifact
   entries, missing `schema_version`, unknown schemas by default, and
   `bot_timeseries.v1` even with `--allow-unknown`.
3. Preserve the limited raw single-artifact inference rules exactly:
   - posture infers `executive_posture`;
   - control review infers `control_review`;
   - scorecard index infers `soc_triage`;
   - mover, entity scorecard, and scorecard packet require explicit
     `--report-type` or wrapper `report_type`.
4. Require `--report-type` for raw arrays.
5. Resolve options in the design order:
   - wrapper `report_type`, when present, must be a string;
   - wrapper and CLI `report_type` must match when both are supplied;
   - CLI `--title` and `--limit` override wrapper values with warnings;
   - wrapper `limit` and CLI `--limit` must be positive integers;
   - display limits must not affect required-artifact validation.
6. Render scope near the top of every report:
   - wrapper `scope_label` wins for presentation only;
   - absent wrapper scope uses unambiguous selected artifact `scope`;
   - absent, unknown, or mixed artifact scope renders as unavailable or mixed
     with a visible warning.
7. Add tests for the grammar, option precedence, display-limit behavior, and
   scope rendering.

## Acceptance Criteria

- [ ] Every accepted input shape normalizes deterministically.
- [ ] Report intent fails closed when missing or ambiguous.
- [ ] CLI presentation overrides warn in stderr and rendered report warnings.
- [ ] Scope display follows the wrapper/artifact rules without affecting
  compatibility.
- [ ] `test_command` passes.

## Related Files

- `skills/bot-insights/scripts/render_report.py` -- Modify.
- `tests/test_skill_scripts.py` -- Modify.
