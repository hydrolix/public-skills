---
task: Complete report-specific Markdown semantics
priority: high
recommended_model: sonnet
estimated_effort: large
depends_on: ["05-renderer-markdown-evidence-warnings"]
test_command: "uv run python -m unittest discover -s tests -k render_report"
---

# Task 06: Complete Report-Specific Markdown Semantics

## Before Starting

Read these files:

- `docs/bot-insights-reporting-design.md` -- report type sections, visualization source rules, and domain-specific guardrails.
- `skills/bot-insights/scripts/render_report.py` -- report renderers.
- `tests/test_skill_scripts.py` -- existing report-renderer tests.

## Context

Each report type has a different audience and evidence boundary. The renderer
may summarize emitted artifact fields, but it must not recompute scores, invent
causality, or make missing evidence look safe.

## Requirements

1. Executive posture:
   - render title, scope, summary, metric deltas, current/baseline rows, optional
     compatible scorecard ranking, optional compatible movers, confidence, and
     evidence limits;
   - avoid causal language for movement-only artifacts.
2. SOC triage:
   - render index-only degraded mode as ranking-only with visible warnings;
   - render domain matrix, security evidence, missing-feature evidence, and
     confidence reasons only when compatible scorecards are available;
   - never render empty scorecard-dependent sections as if evidence exists.
3. Control review:
   - render target, before/after/expected windows, target effects, collateral
     checks, displacement checks, confidence, and evidence limits;
   - use effectiveness-review language, not causal-proof language.
4. Scorecard brief:
   - render entity identity, score, confidence, domain scores, feature evidence,
     not-evaluated features, and artifact-provided `recommended_next_steps`;
   - do not invent next steps or follow-up questions outside analyst notes.
5. Crawler governance:
   - use only eligible `crawler_governance` features;
   - require structured `rowset_scope` or `feature_provenance` before rendering
     generic `rate_429_delta_high` or `rate_5xx_delta_high` as crawler findings;
   - list missing crawler inputs and provenance gaps in evidence limits;
   - preserve compatible index order after filtering, or label input order when
     no compatible index exists.
6. Edge/Ops impact:
   - use only eligible `cache_busting` and `origin_impact` features;
   - list missing operational inputs in evidence limits;
   - preserve compatible index order after filtering, or label input order when
     no compatible index exists.
7. Domain matrix:
   - render emitted numeric zero scores as zero, not unavailable;
   - use evidence limits for missing inputs and ambiguity.
8. Add tests for each report type, degraded modes, ordering labels, no-causal
   language, missing-feature disclosure, and scorecard brief next steps.

## Acceptance Criteria

- [ ] All six MVP report types render the sections required by the design.
- [ ] Degraded modes are explicit and never imply safety.
- [ ] Report-specific entity ordering follows index order or labeled input
  order as appropriate.
- [ ] Crawler and Edge/Ops findings use only eligible emitted evidence.
- [ ] `test_command` passes.

## Related Files

- `skills/bot-insights/scripts/render_report.py` -- Modify.
- `tests/test_skill_scripts.py` -- Modify.
