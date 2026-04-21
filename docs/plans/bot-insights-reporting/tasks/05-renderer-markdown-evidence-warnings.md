---
task: Complete Markdown safety, evidence limits, and warning diagnostics
priority: high
recommended_model: sonnet
estimated_effort: medium
depends_on: ["04-renderer-artifact-compatibility"]
test_command: "uv run python -m unittest discover -s tests -k render_report"
---

# Task 05: Complete Markdown Safety, Evidence Limits, And Warnings

## Before Starting

Read these files:

- `docs/bot-insights-reporting-design.md` -- Markdown escaping, reliability rules, evidence limits, and error handling.
- `skills/bot-insights/scripts/render_report.py` -- Markdown rendering, warnings, and CLI `main`.
- `tests/test_skill_scripts.py` -- renderer tests and CLI-test patterns if present.

## Context

Reports are portable artifacts. User-controlled strings must be plain text, and
warnings must be visible both in CLI diagnostics and in the rendered report.
Missing evidence must be explicit rather than treated as zero or safe.

## Requirements

1. Implement Markdown escaping for user-controlled values:
   - backslash, backtick, asterisk, underscore, braces, brackets, parentheses,
     hash, plus, minus, period, exclamation mark, pipe, angle brackets, and
     ampersand;
   - line breaks inside table cells become spaces;
   - user-supplied links, images, autolinks, inline HTML, and raw Markdown are
     not rendered as markup.
2. Apply escaping consistently to titles, scope labels, entity names, paths,
   query strings, policy IDs, analyst-note fields, and citation labels.
3. Expand the evidence-limits section so every report includes:
   - artifact ID and schema;
   - parent artifact metadata when present;
   - table, scope, confidence, confidence reasons, and interpretation
     constraints;
   - not-evaluated feature names and missing inputs;
   - producer-limit metadata and source-population caveats when relevant.
4. Implement schema-specific metadata warnings:
   - posture and scorecards warn on missing current/baseline windows;
   - control reviews warn on missing before/after windows;
   - control reviews warn on missing or unknown `expected_basis` when target
     effects contain expected values;
   - control reviews warn on missing `expected_window` when
     `expected_basis` is `before_window` or `external_model`;
   - movers warn on missing `dimension` or `metric` without inventing window
     requirements.
5. Ensure CLI warnings are written to stderr and rendered in the report warning
   section.
6. Ensure unwritable output paths return a non-zero CLI exit with a stderr
   error.
7. Add tests for escaping, evidence-limit content, metadata warnings, stderr
   warnings, and unwritable output behavior.

## Acceptance Criteria

- [ ] Markdown output treats all artifact and note strings as plain text.
- [ ] Evidence limits preserve missing evidence, confidence details, and
  producer-limit caveats.
- [ ] Warnings are visible in both report output and CLI stderr.
- [ ] Control-review metadata warnings match the accepted design.
- [ ] `test_command` passes.

## Related Files

- `skills/bot-insights/scripts/render_report.py` -- Modify.
- `tests/test_skill_scripts.py` -- Modify.
