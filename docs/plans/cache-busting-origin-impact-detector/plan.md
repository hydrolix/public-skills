# Cache-Busting Origin-Impact Detector Plan

## Goal

Implement the v1 deterministic cache-busting and origin-impact detector for the
`bot-insights` skill from
`docs/cache-busting-origin-impact-detector-design.md`.

The implementation should produce `cache_origin_impact_report.v1` from
already-aggregated JSON rows, prefer path-summary evidence, expose mechanical
feature evidence and confidence reasons, and avoid any local Hydrolix client or
credential handling.

## Scope

- Add Bot Insights reference documentation for the detector, metadata-aware SQL
  template guidance, path-summary first selection, raw fallback boundaries, and
  known v1 limitations.
- Add `skills/bot-insights/scripts/cache_origin_impact.py` as a standalone,
  dependency-light normalizer and detector.
- Accept dictionary rows, MCP-style `columns` plus `rows`, saved JSON, pasted
  JSON, and in-process trusted context.
- Support the v1 path-grain dimension sets from the design.
- Compute canonical cache-busting, cache-miss movement, origin-pressure, and
  bot-attributable metrics.
- Emit candidate scores, bands, feature contributions, finding types,
  confidence labels, confidence reasons, limitations, `not_evaluated`, optional
  response-byte metadata, and optional host-scope bot-summary context.
- Keep standalone JSON confidence capped at `medium`; allow `high` confidence
  only through a reviewed in-process trusted-context argument.

## Non-Goals

- No local Hydrolix queries, database clients, credentials, or connection
  configuration.
- No opaque ML, causal claims, mitigation recommendations, or scorecard export.
- No v1 non-path-grain candidate lists for resource, ASN, CDN, bot owner,
  exact-status, country, edge POP, user-agent, or host-only rollups.
- No direct-MCP wrapper unless a future task establishes repo ownership for that
  runtime surface.

## Task Package

This file is the single source of truth for Ralph execution. The installed
Ralph runner parses executable tasks from this checklist.

- [ ] **Task 01**: Add Bot Insights cache-origin reference documentation `tasks/01-cache-origin-reference.md`
- [ ] **Task 02**: Add normalizer input parsing and validation foundation `tasks/02-normalizer-input-validation.md`
- [ ] **Task 03**: Implement metric derivation and baseline normalization `tasks/03-metric-derivation.md`
- [ ] **Task 04**: Implement detector scoring and report assembly `tasks/04-detector-scoring-report.md`
- [ ] **Task 05**: Implement confidence, limitations, and optional context handling `tasks/05-confidence-limitations-context.md`
- [ ] **Task 06**: Wire CLI behavior and end-to-end tests `tasks/06-cli-and-e2e-tests.md`
- [ ] **Task 07**: Finalize skill docs and examples `tasks/07-docs-examples.md`

## Validation Strategy

- Run focused unit tests through `uv run python tests/test_skill_scripts.py -q`
  after script changes.
- Run `uv run python scripts/validate-skill-examples.py` after markdown or SQL
  example changes.
- Keep every task verification deterministic and non-interactive.

## Risks

- Summary-table aggregate-state syntax varies by deployment; documentation must
  state that SQL templates are metadata-aware examples and must not infer merge
  functions from column names.
- Saved or pasted JSON cannot prove provenance; confidence logic must ignore
  caller-supplied trust fields.
- Contribution percentages are only valid with complete-scope denominators; the
  normalizer must withhold contribution evidence when source-limited.
- Bot-summary context is host-scope context only and must not be presented as
  path-level candidate evidence.
