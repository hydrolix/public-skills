---
task: Add demo examples and skill documentation links
priority: medium
recommended_model: haiku
estimated_effort: small
depends_on: ["07-renderer-html-svg"]
test_command: "uv run python -m unittest discover -s tests && uv run python scripts/validate-skill-examples.py ."
---

# Task 08: Add Demo Docs And Examples

## Before Starting

Read these files:

- `docs/bot-insights-reporting-design.md` -- accepted design and deferred decisions.
- `skills/bot-insights/SKILL.md` -- skill entrypoint and reference map.
- `skills/bot-insights/references/scorecard-analysis.md` -- scorecard workflow context.
- `skills/bot-insights/references/baseline-comparison.md` -- posture and control-review workflow context.

## Context

After the renderer behavior is stable, normal skill users need a discoverable
path from Bot Insights analysis to saved report artifacts. Demo examples should
be deterministic and should not require Hydrolix access.

## Requirements

1. Add a concise reporting reference or reporting section that explains:
   - the renderer consumes saved artifacts only;
   - accepted input shapes;
   - supported report types;
   - Markdown and HTML commands;
   - warning and evidence-limit expectations;
   - no direct Hydrolix querying from the renderer.
2. Link the reporting reference from `skills/bot-insights/SKILL.md` only after
   Tasks 03-07 are complete.
3. Add small demo fixture JSON files or documented inline examples for:
   - executive posture;
   - SOC triage with scorecard packet;
   - control review;
   - crawler governance or Edge/Ops impact.
4. Keep examples valid JSON when they are complete examples. Clearly label
   intentionally partial fragments.
5. Add or update tests only if executable examples are introduced.
6. Run the repository validator and keep existing validator warnings from
   unrelated SQL examples unchanged unless this task directly edits those
   blocks.

## Acceptance Criteria

- [ ] Users can find the report workflow from `SKILL.md`.
- [ ] Demo examples render with `render_report.py` without Hydrolix access.
- [ ] JSON examples are valid or explicitly labeled as fragments.
- [ ] Documentation preserves the artifact-only boundary.
- [ ] `test_command` passes.

## Related Files

- `skills/bot-insights/SKILL.md` -- Modify.
- `skills/bot-insights/references/` -- Modify or add one reporting reference.
- `skills/bot-insights/examples/` -- Create only if using fixture files.
- `tests/test_skill_scripts.py` -- Modify only if examples become executable tests.
