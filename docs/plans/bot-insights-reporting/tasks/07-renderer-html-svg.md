---
task: Build self-contained HTML and SVG report output
priority: medium
recommended_model: sonnet
estimated_effort: large
depends_on: ["06-renderer-report-semantics"]
test_command: "uv run python -m unittest discover -s tests -k render_report"
---

# Task 07: Build Self-Contained HTML And SVG Output

## Before Starting

Read these files:

- `docs/bot-insights-reporting-design.md` -- HTML output and visualization requirements.
- `skills/bot-insights/scripts/render_report.py` -- current HTML and SVG helpers.
- `tests/test_skill_scripts.py` -- current HTML smoke tests.

## Context

Markdown is the portable default, but HTML is the primary demo format for the
full MVP. HTML must be self-contained, escaped, and usable by opening the file
directly in a browser.

## Requirements

1. Keep HTML output as a single self-contained document:
   - inline CSS only;
   - inline SVG only;
   - no external fonts, scripts, images, or CDN assets.
2. Escape all user-controlled content in HTML, including content converted from
   Markdown tables and analyst notes.
3. Add SVG primitives for:
   - metric delta cards;
   - current versus baseline bars;
   - scorecard ranking bars;
   - mover contribution bars;
   - domain score matrix;
   - control before/after/expected bars.
4. Use SVG scaling only for visual layout. Do not recompute artifact metrics,
   scores, confidence, rates, or rankings.
5. Add accessible chart labels and visible value labels.
6. Ensure every report type either renders the relevant charts or visibly
   explains why a chart is skipped because required fields are unavailable.
7. Add focused tests for:
   - HTML escaping;
   - absence of external assets or scripts;
   - expected SVG presence for report types with chartable data;
   - skipped-chart warnings when required fields are unavailable.

## Acceptance Criteria

- [ ] HTML reports are self-contained and render without network access.
- [ ] SVG charts cover the MVP visualization types.
- [ ] HTML output does not expose artifact strings as raw markup.
- [ ] Chart output uses artifact-provided values only.
- [ ] `test_command` passes.

## Related Files

- `skills/bot-insights/scripts/render_report.py` -- Modify.
- `tests/test_skill_scripts.py` -- Modify.
