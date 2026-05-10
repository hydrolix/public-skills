# Bot Insights report rendering → Jinja2 (delete the bespoke HTML path)

> Plan. New branch off `main` after `skill-deployment-alignment` merges. Routes
> every Bot Insights report through `report_engine` (Jinja2), ports the one
> remaining stub (`control_review`), and deletes the ~1000 lines of bespoke
> Python HTML generation in `scripts/render_report.py`. Output styling matches
> the engine-rendered reference at `/private/tmp/check-fleet.html`:
> progressive disclosure, purpose strip, score-landscape hero, narrative slots
> for LLM prose, human-readable labels, `0.00%` percentages, deltas vs prior
> period throughout.

## Context

The `bot-insights` skill has two parallel renderers. The modern one,
`skills/bot-insights/scripts/report_engine/`, is a Jinja2 engine with 22
working macros (`executive_summary`, `score_landscape`, `report_purpose`,
`findings`, `coverage_table`, `entities_table`, `method`, ...) and 6 of 7
context modules fully implemented. The legacy one, `scripts/render_report.py`,
hand-assembles HTML via ~1000 lines of f-string templates spread across 21
`html_*` functions plus 13 `md_*` markdown builders and a regex-based
`markdown_to_simple_html()`. Live smoke runs against the just-shipped
`skill-deployment-alignment` branch confirmed the legacy path is what gets
rendered today: `_render_via_engine` at `render_report.py:4172` falls back to
legacy whenever input isn't a `bot_report_input.v1` wrapper, the registry
misses, or assembly raises.

The directive: every report renders through Jinja2; no bespoke Python
templating remains. The engine is already ~95% there; this plan finishes the
port (`control_review`), consolidates a few shared utilities, gates the
migration with a parity-diff harness, then deletes the legacy code.

**Decisions confirmed:**

- Branch: new branch off `main` **after `skill-deployment-alignment` merges**.
- Legacy path: delete entirely.
- Scope: all 7 report types
  (`executive_posture`, `control_review`, `scorecard_brief`,
  `scorecard_entity_review`, `soc_triage`, `crawler_governance`,
  `edge_ops_impact`).
- `--format markdown` survives via new Jinja2 `.md.j2` templates (no Python
  markdown builders remain).
- Parity-diff harness is throwaway — lives under `scripts/_diff/`, deleted
  with the legacy code.
- If `render_report.py` still exceeds the 500-line gate after deletion, split
  into `render_report.py` (CLI entry) + `wrapper_loader.py` (input
  normalization).

## What's already in place (reuse these)

- `report_engine/formatters.py`: `pct2()` (canonical `0.00%`),
  `signed_pct(digits=2)`, `signed_pp()`, `format_share_pct()`,
  `normalize_percents()`.
- `report_engine/humanize.py`: `ENTITY_TYPE_LABELS`, `BAND_LABELS`,
  `CONFIDENCE_LABELS`, `RULE_STATUS_LABELS`, `humanize_identifier()`.
- `report_engine/charts.py:15-99`: `score_gauge_svg()`, `score_bar_svg()`.
- `scripts/baselines.py:84-93`: `direction()`, `pct_delta()`.
- `report_engine/templates/macros/`: 22 working macros.
- `report_engine/contexts/__init__.py`: `REPORT_TYPE_REGISTRY` already
  registers all 7 types.

## Phases

### Phase 0 — Baseline snapshot

Capture legacy output for every fixture under `tests/fixtures/` and
`skills/bot-insights/examples/` to `.snapshots/legacy_baseline/<fixture>.html`.
This is the objective oracle phase 3 diffs against.

- **Verify:** snapshot count = fixture count. No code changes.

### Phase 1 — Shared utility consolidation (no behavior change)

Move what's needed by the engine out of `render_report.py` so phase 2 can
build `control_review` on top of canonical surfaces. Legacy keeps working by
re-importing.

- Move `rule_label_parts()` from `render_report.py:194-227` into
  `report_engine/humanize.py`.
- Move `human_metric_name()` and `METRIC_LABELS` (consolidate the
  `contexts/executive_posture.py:39-50` copy and the
  `contexts/scorecard_brief.py:49-63` copy) into `report_engine/humanize.py`.
  Both contexts import from there.
- New `report_engine/deltas.py` wrapping `baselines.py` `pct_delta()` /
  `direction()` plus a `signed_delta_pp()` helper. Stand-in for the
  ad-hoc `(current - baseline) / max(baseline, 1.0) * 100` repeated across
  context modules — touch only the duplicate sites where it lives today.
- Audit templates for hardcoded `:.0%`, `:.2f`, `{:+.0f}%` patterns; replace
  with `|pct2`, `|signed_pct`, `|signed_pp` filters consistently.
- **Verify:** `uv run pytest tests/test_report_engine.py tests/test_skill_scripts.py`
  passes (baseline: 369 passed + 4 skipped). Re-run phase 0 snapshot
  capture and `diff` against the baseline — zero textual diffs.

### Phase 2 — Port `control_review` to the engine

The one stub. The audit understates its complexity: control_review is
multi-artifact (posture + control + mover + timeseries) with companion
selection logic at `render_report.py:931-1142`
(`companion_compatible()`, `shared_metadata_matches()`).

- Extract `select_control_companions()` from `render_report.py` into
  `report_engine/contexts/_shared.py`. Unit-test independently.
- Rewrite `report_engine/contexts/control_review.py` (45 → ~600 lines,
  comparable shape to `executive_posture.py`). Implement `assemble()`,
  `prepare()`, `NOTE_ID_TO_SLOT`.
- New `report_engine/templates/reports/control_review.html`. Reuses
  `score_landscape`, `executive_summary`, `operational_interpretation`,
  `coverage_table`, `method`.
- New `report_engine/templates/macros/control_bars.html` for the
  before/after/expected SVG bar trio (ports the visual from
  `html_control_bars` at `render_report.py:3521-3605`).
- **Verify:** new tests `tests/test_report_engine.py::test_control_review_*`
  assert presence of oracle class names (`gauge-card`, `narrative-slot`,
  `landscape-grid`). All 7 report types now render through the engine when
  forced.

### Phase 3 — Parity-diff harness

The migration gate. Without this, engine output can silently diverge from
legacy and snapshot tests against the engine would still pass.

- New `scripts/_diff/parity_check.py`: iterates every wrapper fixture,
  renders via both paths, normalizes whitespace + applies an explicit
  class-rename allowlist (e.g., `fleet-kpi` → `kpi-card`), reports unified
  diff per fixture.
- **Verify:** zero non-allowlisted diffs. Each documented intentional
  difference (e.g., new narrative slot present in engine, absent in legacy)
  has a one-line justification in the script's allowlist.

### Phase 4 — Force engine routing

The point of no return.

- `_render_via_engine` at `render_report.py:4172`: replace the `return None`
  branches with `raise ReportError(...)` for the 7 wrapper schemas. The
  `is_wrapper` short-circuit stays for raw-artifact mode (out of scope).
- **Verify:** parity harness clean. Full test suite passes.

### Phase 5 — Delete legacy renderers

The substantive deletion.

- Remove from `render_report.py`:
  - 21 `html_*` functions (range ~L2565-L3823): `html_metric_delta_cards`,
    `html_current_baseline_bars`, `html_ranking_bars`,
    `html_scorecard_score_bars`, `html_scorecard_domain_bars`,
    `html_scorecard_feature_cards`, `html_mover_bars`, `html_control_bars`,
    `html_window_timeline`, `html_timeseries_cards`,
    `html_scorecard_overall_gauge`, `html_scorecard_context_panel`,
    `html_domain_matrix`, `html_fleet_kpis`, `html_fleet_findings`,
    `html_fleet_coverage`, `html_fleet_ranked_entities`,
    `html_fleet_next_steps`, `html_fleet_method`, `html_chart_sections`,
    `html_scorecard_fleet_report`.
  - 13 `md_*` builders and `render_markdown()` dispatcher at
    `render_report.py:1362`.
  - `markdown_to_simple_html()` at `render_report.py:4039`.
  - Inline `<style>` heredoc at `render_report.py:3956-4029`.
  - `_render_executive_posture_via_engine` backwards-compat shim at
    `render_report.py:4222`.
- Replace `render()` body with a thin shim that builds a wrapper and calls
  `_render_via_engine` (now guaranteed to succeed for the 7 types).
- New Jinja2 `.md.j2` markdown templates per report type under
  `report_engine/templates/reports/` (or `templates/reports/markdown/` —
  pick whichever keeps the directory legible). They render via the same
  `assemble()` + `prepare()` contexts; only the output format differs.
- If `render_report.py` exceeds 500 lines after deletion, split:
  `render_report.py` (argparse + entry), `wrapper_loader.py`
  (`load_report_input`, `resolve_options`, `report_type` inference,
  `compose_wrapper`). Likely needed — `load_report_input` and friends
  alone are 300+ lines.
- **Verify:** commit-gate clean. Full test suite. Parity harness still
  green (run from a temp checkout of the pre-deletion commit if needed).

### Phase 6 — CSS consolidation

- Delete dead `fleet-*` selectors from
  `report_engine/templates/_styles.css`. Use
  `grep -rL '<selector>' templates/` to identify selectors no template
  references; remove those.
- Visually diff a phase-0 snapshot against the post-phase-6 render of the
  same fixture; both should be visually equivalent or document the
  difference.
- **Verify:** every selector left in `_styles.css` is referenced by at
  least one template.

### Phase 7 — Test updates

- Rewrite `tests/test_report_engine.py` assertions that target legacy class
  names (lines ~L152/197/201/203/214/218/220/257/261/263/274/278/280/314/319/321/323/336/339/341/343/360 — confirm during implementation).
- Add `control_review` fixture-based tests (assemble + render + class
  assertions).
- Add a class-name presence audit test: for each report type, assert the
  rendered HTML contains a non-trivial subset of the oracle class set
  (`narrative-slot`, `gauge-card`, `report-purpose`, `prose`, plus
  report-type-specific selectors like `landscape-grid` for fleet reports).
- **Verify:** test count climbs from 369 to ~380+ passing.

### Phase 8 — Manual 12-invocation smoke gate

Re-run the smoke matrix from `docs/plans/skill-deployment-alignment/plan.md`
section 7 against demo.trafficpeak.live and acme. All 12 should
render HTML matching the reference style.

- File-size envelope sanity check per report type (catches catastrophic
  empty-template regressions).
- Class-set diff: `diff <(grep -o 'class="[^"]*"' /private/tmp/check-fleet.html | sort -u) <(grep -o 'class="[^"]*"' <new>.html | sort -u)` — exposes class-set divergence in one line.
- SOC graceful fallback (acme) and `edge_ops_impact --include-paths`
  graceful fallback must still fire the expected stderr warnings.

## Critical files

| Phase | File | Purpose |
|-------|------|---------|
| 0 | `.snapshots/legacy_baseline/` (new, gitignored) | Pre-change oracle |
| 1 | `report_engine/humanize.py` (MOD) | Absorb `rule_label_parts`, `METRIC_LABELS`, `human_metric_name` |
| 1 | `report_engine/deltas.py` (new) | Canonical `pct_delta`, `signed_delta_pp` |
| 1 | `report_engine/contexts/executive_posture.py` (MOD) | Import shared `METRIC_LABELS` |
| 1 | `report_engine/contexts/scorecard_brief.py` (MOD) | Import shared `METRIC_LABELS` |
| 1 | `render_report.py` (MOD) | Re-import moved utilities |
| 1 | `report_engine/templates/` (MOD) | Replace ad-hoc `:.0%`/`:.2f` with filters |
| 2 | `report_engine/contexts/_shared.py` (MOD) | Add `select_control_companions` |
| 2 | `report_engine/contexts/control_review.py` (rewrite) | Implement assemble + prepare |
| 2 | `report_engine/templates/reports/control_review.html` (new) | Control review template |
| 2 | `report_engine/templates/macros/control_bars.html` (new) | Before/after/expected SVG |
| 3 | `scripts/_diff/parity_check.py` (new, temporary) | Migration gate |
| 4 | `render_report.py` (MOD) | Force engine routing |
| 5 | `render_report.py` (MOD, large deletion) | Remove `html_*`, `md_*`, `markdown_to_simple_html`, inline CSS |
| 5 | `wrapper_loader.py` (new, if needed) | Split if file-size gate trips |
| 5 | `report_engine/templates/reports/*.md.j2` (new) | Markdown output templates |
| 6 | `report_engine/templates/_styles.css` (MOD) | Drop dead selectors |
| 7 | `tests/test_report_engine.py` (MOD) | Update legacy-class assertions; add control_review tests; add class-presence audit |

## Out of scope

- Adding new report types beyond the existing 7.
- Refactoring `bot_insights_report.py` orchestration or the wrapper assembly
  it performs.
- Changing the `bot_report_input.v1` schema or any artifact `schema_version`
  constants at `render_report.py:18-60`.
- The raw-artifact (non-wrapper) rendering path — `_render_via_engine`'s
  `is_wrapper` short-circuit stays. Migrating that path is a follow-up.
- Rewriting chart primitives in `report_engine/charts.py` (the oracle proves
  they work).
- Modifying `scripts/baselines.py` — wrap it in `deltas.py`.
- Adding new macros for slot types the oracle doesn't already exercise.

## Verification

1. **Per-phase tests:**
   - Phase 0: snapshot count = fixture count.
   - Phase 1: `uv run pytest tests/test_report_engine.py tests/test_skill_scripts.py` clean (369/4); phase-0 snapshots re-render identically.
   - Phase 2: new `test_control_review_*` tests pass; class-name audit passes for control_review.
   - Phase 3: parity harness reports zero non-allowlisted diffs.
   - Phase 4: parity harness still clean; full test suite clean.
   - Phase 5: commit-gate clean; full test suite clean; parity harness clean (run from pre-deletion commit checkout).
   - Phase 6: every `_styles.css` selector referenced by ≥1 template.
   - Phase 7: test count ≥ 380 passing.

2. **End-state class-name audit:** build a per-report-type expected-class
   matrix as a test fixture and assert each smoke output contains its
   expected subset (`narrative-slot`, `gauge-card`, `report-purpose`,
   `prose`, plus type-specific like `landscape-grid` for fleet reports).

3. **End-state file-size envelope:** per-report-type byte ranges as a smoke
   sanity check (fleet ~35 KB ± 10 KB; brief ~18 KB ± 5 KB).

4. **Manual smoke gate (phase 8):** 12 invocations against
   demo.trafficpeak.live + acme; visual parity with
   `/private/tmp/check-fleet.html`; SOC + path-grain graceful fallbacks
   still fire.

5. **Reused functions preserved:** after phase 5, `pct2`, `signed_pct`,
   `pct_delta`, `direction`, `score_gauge_svg`, `score_bar_svg`,
   `ENTITY_TYPE_LABELS`, `rule_label_parts` (now in `humanize.py`) all
   continue to exist and are imported only from `report_engine/`.
