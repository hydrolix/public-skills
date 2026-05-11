# Bot Insights report rendering → Jinja2 (delete the bespoke HTML/Markdown path)

> Plan v2. Addresses [PLAN REVIEW v1] findings: splits the work into four
> sequential PRs, resolves the raw-artifact contradiction, adds a durable
> regression suite (HTML *and* Markdown), defines a rollback per milestone,
> and corrects validation commands for this repo.
>
> Routes every Bot Insights *wrapper-mode* report through `report_engine`
> (Jinja2) for both `--format html` and `--format markdown`, then deletes
> the bespoke Python rendering in `scripts/render_report.py`.

## Goal and scope contract

**In scope:** wrapper-mode rendering (`schema_version: bot_report_input.v1`)
for all 7 report types (`executive_posture`, `control_review`,
`scorecard_brief`, `scorecard_entity_review`, `soc_triage`,
`crawler_governance`, `edge_ops_impact`) and both output formats
(`--format html`, `--format markdown`).

**Explicitly out of scope:** raw-artifact rendering (the
`_render_via_engine` `is_wrapper` short-circuit at
`render_report.py:4191-4197` stays). After this plan, raw-artifact mode is
the *only* surviving caller of `markdown_to_simple_html()` and a curated
minimal subset of legacy helpers needed to satisfy it. **Resolves [HIGH-1]
in v1 review** — the original "every report renders through Jinja2"
language was inaccurate; the goal here is "every wrapper-mode report."
Raw-artifact migration is tracked as a follow-up plan.

**Decision deferred to milestone M4:** whether to retire raw-artifact mode
entirely as part of this branch. Default: keep it. If during M4 we can
confirm no caller exercises raw mode in the last 90 days, we delete the
short-circuit and the associated helpers in the same PR.

## Current state (verified)

- `scripts/render_report.py`: **4336 lines** (not ~1000). Contains 21
  `html_*` functions (lines ~2565–3823), 20 `md_*` functions (not 13),
  `render_markdown()` dispatcher, `markdown_to_simple_html()` regex
  builder, inline `<style>` heredoc, and the `_render_via_engine` shim.
- `report_engine/` has 22 macros, contexts for 6 of 7 report types
  (`control_review.py` is a 45-line stub), `formatters.py`, `humanize.py`,
  `charts.py`, `theme.py`, `verdicts.py`, `findings.py`, `markdown.py`,
  `volume_impact.py`, `scorecards.py`.
- HTML routing through the engine is **partial**: only
  `executive_posture`, `soc_triage`, `crawler_governance`, `edge_ops_impact`
  reach `_render_via_engine` (see `render_report.py:4275-4280`).
  `scorecard_brief`, `scorecard_entity_review`, `control_review` always
  hit legacy. Plan v1 missed this.
- Markdown routing through the engine: **zero**. All 7 types render via
  `render_markdown()` (`render_report.py:1362`), which dispatches to the
  20 `md_*` builders.
- Baseline: `python3 -m pytest tests/` → 369 passed + 4 skipped.
  `python3 -m unittest discover -s tests` (the CI command) → 344 tests
  pass. Both are the validation contract; pytest count is higher because
  parameterized tests expand.
- **No `pyproject.toml`, no `Makefile`, no `tools/commit_gate_local.sh`.**
  Plan v1 referenced `uv run pytest` and a 500-line commit gate; neither
  exists. Validation commands in v2 use what CI actually runs.

## Reuse surface (unchanged from v1)

- `report_engine/formatters.py`: `pct2()`, `signed_pct()`, `signed_pp()`,
  `format_share_pct()`, `normalize_percents()`.
- `report_engine/humanize.py`: `ENTITY_TYPE_LABELS`, `BAND_LABELS`,
  `CONFIDENCE_LABELS`, `RULE_STATUS_LABELS`, `humanize_identifier()`.
- `report_engine/charts.py`: `score_gauge_svg()`, `score_bar_svg()`.
- `scripts/baselines.py`: `direction()`, `pct_delta()`.
- `report_engine/contexts/__init__.py:REPORT_TYPE_REGISTRY` covers all 7
  types (control_review is registered but its module is a stub).

## Milestone structure (four sequential PRs)

```
M1: foundation + control_review port    ──►  no behavior change yet for live reports
M2: durable HTML parity + force HTML routing for all 7 types ──►  HTML legacy unreachable
M3: durable Markdown parity + force Markdown routing for all 7 types ──►  Markdown legacy unreachable
M4: delete legacy + CSS cleanup + raw-artifact decision ──►  the substantive deletion
```

Each milestone is its own PR with green CI at the boundary and a defined
rollback (`git revert <merge-commit>`). The branch `jinja2-rendering`
hosts only M1; M2–M4 land on follow-on branches off the previous
milestone's merge commit.

---

## Milestone 1 — Foundation + control_review port

**Branch:** `jinja2-rendering` (this branch).
**Behavior change:** none for users — legacy renders all formats.
The engine gains a working `control_review` module and the shared
utilities a third party port would need.

### M1.1 Shared utility consolidation (no behavior change)

- Move `rule_label_parts()` from `render_report.py:194-227` into
  `report_engine/humanize.py`. Re-export from `render_report.py` so
  existing callers still work.
- Move `human_metric_name()` and consolidate `METRIC_LABELS` (currently
  duplicated in `contexts/executive_posture.py:39-50` and
  `contexts/scorecard_brief.py:49-63`) into `report_engine/humanize.py`.
  Update both contexts to import from there.
- Add `report_engine/deltas.py` wrapping `baselines.py:pct_delta()` /
  `direction()` plus a `signed_delta_pp()` helper. Replace the ad-hoc
  `(current - baseline) / max(baseline, 1.0) * 100` pattern only in the
  files where it lives today (no template churn yet).
- **Out of M1.1:** template filter migration (`:.0%` → `|pct2`, etc.).
  Deferred to M1.3 — keeps M1.1 a pure refactor.

**Verify:**
- `python3 -m unittest discover -s tests` clean.
- `python3 -m pytest tests/` reports 369 passed + 4 skipped.
- New unit tests for `deltas.signed_delta_pp()` and `humanize.rule_label_parts()` after the move.

### M1.2 Port `control_review` to the engine

- Extract `select_control_companions()` and supporting helpers
  (`companion_compatible()`, `shared_metadata_matches()`) from
  `render_report.py:931-1142` into `report_engine/contexts/_shared.py`.
- **Mandatory new unit tests** in `tests/test_report_engine.py` covering
  `select_control_companions`:
  - happy path: posture + control + mover + timeseries selected.
  - missing optional companions: control alone resolves without warning.
  - missing required posture: raises with a structured error.
  - incompatible companion (mismatched window): rejected with a warning
    in `ctx.warnings`.
  - same-packet metadata vs. shared-metadata match: both accepted.
  - multiple optional companions: deterministic tiebreak.
  These tests land **before** the rewrite calls into `_shared.py` so the
  extracted function is covered by behavior, not implementation.
- Rewrite `report_engine/contexts/control_review.py`. **Size estimate
  revised from v1**: comparable contexts (`executive_posture.py` is
  ~800 lines) suggest the realistic target is ~750–900 lines, not 600.
  Implement `assemble()`, `prepare()`, `NOTE_ID_TO_SLOT`.
- New `report_engine/templates/reports/control_review.html`. Reuses
  `score_landscape`, `executive_summary`, `operational_interpretation`,
  `coverage_table`, `method`.
- New `report_engine/templates/macros/control_bars.html` ports the
  before/after/expected SVG bar trio from `html_control_bars`
  (`render_report.py:3521-3605`).
- **Routing stays unchanged in M1.** `control_review` HTML still goes
  through legacy because the dispatcher in `render_report.py:4275-4280`
  doesn't list it yet. M2 turns it on.

**Verify:**
- Companion-selection unit tests pass.
- New `tests/test_report_engine.py::test_control_review_*` exercises
  `assemble()`, `prepare()`, and rendering against fixtures covering:
  empty companions, missing metadata, displaced expected basis,
  collateral artifacts, warning fan-out.
- Class-name presence: rendered HTML contains `gauge-card`,
  `narrative-slot`, `landscape-grid`, `report-purpose`, `prose`.

### M1.3 Template filter normalization

- Audit templates for hardcoded `:.0%`, `:.2f`, `{:+.0f}%`. Replace with
  `|pct2`, `|signed_pct`, `|signed_pp` filters.
- **Verify:** the snapshot tests in `tests/test_report_engine.py` catch
  any rendered-output diffs. If a filter change shifts output, either
  update the snapshot or fix the filter — never silently accept drift.

### M1 verification gate

- All tests green: `python3 -m unittest discover -s tests` and
  `python3 -m pytest tests/`.
- Manual smoke: render a `control_review` fixture via the engine
  directly (not through `render_report.py`) and visually inspect the
  output against `/private/tmp/check-fleet.html`.
- File-size envelope sanity: control_review HTML between 15–40 KB.

### M1 rollback

`git revert` the M1 merge commit. Engine `control_review` module reverts
to the 45-line stub; no users were depending on it.

---

## Milestone 2 — Durable HTML parity + force HTML routing

**Branch:** new branch off the M1 merge commit.
**Behavior change:** all 7 report types render HTML via the engine.
Legacy `html_*` functions remain in the file but become unreachable for
wrapper inputs. (Raw-artifact mode still uses them.)

### M2.1 Durable HTML parity suite

**Critical change from v1:** the parity check is *not* a throwaway
script. It becomes a permanent committed test that asserts DOM-level
invariants — not just whitespace-normalized string equality.

**Parser decision:** generation uses Jinja2 (unchanged). Parity testing
uses stdlib **`html.parser`** wrapped in a small `tests/_html_tree.py`
helper (~80 lines, no third-party dependency) that produces a queryable
tree (`find`, `find_all`, `select_class`, `text`, table-row iterator).
No regex fallback — tests fail loudly if the helper can't build a tree,
which is the right failure mode. **Resolves [REVIEW v2 MEDIUM-2]** on
regex-fallback unsafety.

- New `tests/_html_tree.py` (helper, test-only): minimal `HTMLParser`
  subclass building `Node(tag, attrs, children, text)` plus query
  primitives. Documented and unit-tested via `tests/test_html_tree.py`.
- New `tests/test_html_parity.py` parameterized over every wrapper
  fixture under `tests/fixtures/`. For each fixture:
  - Render via legacy `render_html()`.
  - Render via the engine `_render_via_engine()`.
  - Build a tree using `tests/_html_tree.py` for both outputs.
  - Assert **semantic invariants** are preserved across both renders:
    - **Heading set + order:** sequence of `<h1>`, `<h2>`, `<h3>`
      text values must match (order matters — surfaces section
      reordering).
    - **Section presence:** `report-purpose`, `score-landscape`,
      `narrative-slot`, `executive-summary`, `findings`, `coverage`,
      `method` — whichever sections the report type emits.
    - **Numeric content (keyed, not just set):** for every percentage
      or delta, the assertion is keyed by the *nearest enclosing
      heading text* + *containing table caption* (if any) + *row
      label* + *column header*. A percentage that moved from the
      "Bots → Verified" row to "Bots → Unverified" fails. Unordered
      set membership is insufficient. **Resolves [REVIEW v2 MEDIUM-1]**.
    - **Warning lines:** every `WARNING:` stderr line emitted by one
      path is emitted by the other (text-identical).
    - **Table row count:** entities/coverage tables emit the same number
      of rows; row keys (entity_type + identifier) match as sets.
    - **File-size envelope:** byte count within ±15% of legacy.
  - Class-name **rename allowlist** (e.g., `fleet-kpi` → `kpi-card`) is
    a separate `tests/fixtures/parity_allowlist.json` with one-line
    justification per entry. Tests fail closed on any non-allowlisted
    class-set divergence.
- The harness is **permanent** for the life of M2–M3. It is deleted
  alongside the legacy code in M4 (no longer meaningful once one path
  is gone). M4 replaces it with the class-name presence audit from
  Phase 7 v1.

### M2.2 Baseline coverage of every fixture

- Phase 0 (v1) becomes a deterministic fixture-discovery routine inside
  `tests/test_html_parity.py`. The parity test discovers wrapper
  fixtures from `tests/fixtures/`, `skills/bot-insights/examples/`, and
  `tests/snapshots/` and asserts coverage of all 7 report types.
- Explicitly handles four fixture classes:
  - **Wrapper fixtures** (`bot_report_input.v1`): both paths run.
  - **Raw-artifact fixtures**: skipped with a `pytest.skip("raw-mode out of scope")`.
  - **Example fixtures** in `skills/bot-insights/examples/`: included if they're wrapper-mode.
  - **Expected-failure fixtures**: both paths must raise the same `ReportError`.
- Resolves **[MEDIUM-2]** from v1 review (Phase 0 baselining was
  under-specified).

### M2.3 Force HTML routing

- Replace the explicit allowlist at `render_report.py:4275-4280` with
  routing for *all* 7 report types.
- In `_render_via_engine`, change `return None` for known wrapper schemas
  to `raise ReportError("engine assembly failed: ...")`. Keep the
  `is_wrapper` short-circuit at lines 4191–4197 returning `None`
  (raw-artifact fallthrough).

**Verify:**
- `tests/test_html_parity.py` all green.
- Existing test suite green.
- `python3 skills/bot-insights/scripts/render_report.py` against every
  fixture renders HTML matching the parity invariants.

### M2 rollback

`git revert` the M2 merge commit. Routing falls back to the v1
allowlist (4 types via engine, 3 via legacy). HTML parity suite reverts.
Legacy HTML path remains intact and tested by the parity suite that
landed and then reverted with M2.

---

## Milestone 3 — Durable Markdown parity + force Markdown routing

**Branch:** new branch off the M2 merge commit.
**Behavior change:** all 7 report types render Markdown via the engine.
Legacy `md_*` functions remain in the file but become unreachable for
wrapper inputs.

### M3.1 .md.j2 templates per report type

- One Jinja2 markdown template per report type under
  `report_engine/templates/reports/<type>.md.j2`. Discovery is by
  filename suffix; no separate registry needed.
- Reuse the same `assemble()` + `prepare()` contexts as HTML. The
  context object is format-agnostic; only the template chooses
  HTML vs. Markdown.
- Define **autoescape policy** explicitly: Markdown templates set
  `autoescape=False` in `report_engine/render.py`. HTML templates keep
  current behavior. Custom Jinja2 filter `md_escape` (new in
  `report_engine/markdown.py`) escapes pipe characters, backticks, and
  surrounding underscores in user-supplied text — applied at every
  identifier and label interpolation site.
- **Format selection wiring** in `report_engine/render.py`: add
  `render(report_type, fmt, ctx)` where `fmt ∈ {"html", "markdown"}`
  picks `<type>.html` vs. `<type>.md.j2`. Update the caller in
  `render_report.py` to thread the `args.format` choice through.
- Resolves **[MEDIUM-3]** from v1 review (.md.j2 under-specified).

### M3.2 Durable Markdown parity suite + HTML parity refresh

- New `tests/test_markdown_parity.py` mirroring the HTML parity suite.
  Invariants for Markdown:
  - **Heading set:** every `#`, `##`, `###` line.
  - **Section order:** purpose → landscape → narrative → findings →
    coverage → method (where applicable).
  - **Numeric content:** every formatted percentage and delta present
    in both outputs.
  - **Warning lines:** identical between paths.
  - **Table presence:** lines starting with `|` exist in both with the
    same row count.
  - **Byte envelope:** ±15%.
- No "class-name rename" allowlist needed (Markdown has no classes).
- Resolves **[HIGH-2]** and **[MEDIUM-1]** from v1 review.

**HTML parity refresh:** `report_engine/render.py` becomes `fmt`-aware
in M3.1. Any caller in `tests/test_html_parity.py` that invokes
`_render_via_engine()` or `report_engine.render.render()` directly
must be updated to pass `fmt="html"`. After the M3.1 change lands,
rerun `tests/test_html_parity.py` and confirm zero regression. This
catches the "M3 plumbing change silently breaks M2 gate" scenario.
**Resolves [REVIEW v2 LOW-1]**.

### M3.3 Force Markdown routing

- `render_report.py:4269` HTML branch and `render_report.py:4303`
  Markdown branch both flow through `_render_via_engine`, parameterized
  by `args.format`.
- `render_markdown()` and all `md_*` functions stay reachable from
  raw-artifact mode only.

**Verify:**
- `tests/test_markdown_parity.py` all green.
- Existing tests green.

### M3 rollback

`git revert` the M3 merge commit. Markdown reverts to legacy `md_*`
dispatch. HTML routing from M2 still works.

---

## Milestone 4 — Delete legacy + CSS cleanup + raw-artifact decision

**Branch:** new branch off the M3 merge commit.
**Behavior change:** legacy `html_*` and `md_*` code paths deleted from
`render_report.py`. CSS de-duplicated. Optional: raw-artifact mode
retired.

### M4.1 Raw-artifact decision gate

**Committed default: Path B (preserve raw-artifact mode).** Path A
retirement requires a named telemetry source and an owner sign-off
*before* the M4 branch is cut. Mid-milestone ad hoc audits do not
suffice. **Resolves [REVIEW v2 MEDIUM-3]**.

- **Path B (default):** keep `markdown_to_simple_html()`, the
  `is_wrapper` short-circuit at `render_report.py:4191-4197`, and a
  curated minimal set of `html_*`/`md_*` helpers needed to satisfy
  raw-artifact callers. Delete only the wrapper-path legacy code.
- **Path A (conditional, requires pre-approval):** if a named telemetry
  source (CI logs, deployment usage stats, or an owner attestation)
  confirms no raw-artifact callers in a stated time window, delete the
  short-circuit and the helpers it uniquely depends on. The
  pre-approval is captured in the M4 PR description with the data
  source named.
- Pre-M4 audit (informational only; cannot trigger Path A on its own):
  inspect tests, examples, and `bot_insights_report.py` orchestration
  for direct `render_report.py` invocations that bypass the wrapper
  builder. Findings inform the Path A pre-approval request but do not
  substitute for telemetry.

### M4.2 Delete legacy renderers

- Remove from `render_report.py` (Path A removes all of these; Path B
  removes the subset whose only callers were the wrapper path):
  - 21 `html_*` functions (lines ~2565–3823): `html_metric_delta_cards`,
    `html_current_baseline_bars`, `html_ranking_bars`,
    `html_scorecard_score_bars`, `html_scorecard_domain_bars`,
    `html_scorecard_feature_cards`, `html_mover_bars`,
    `html_control_bars`, `html_window_timeline`,
    `html_timeseries_cards`, `html_scorecard_overall_gauge`,
    `html_scorecard_context_panel`, `html_domain_matrix`,
    `html_fleet_kpis`, `html_fleet_findings`, `html_fleet_coverage`,
    `html_fleet_ranked_entities`, `html_fleet_next_steps`,
    `html_fleet_method`, `html_chart_sections`,
    `html_scorecard_fleet_report`.
  - 20 `md_*` builders and `render_markdown()` dispatcher at
    `render_report.py:1362`.
  - `markdown_to_simple_html()` at `render_report.py:4039` (Path A only).
  - Inline `<style>` heredoc at `render_report.py:3956-4029` (Path A
    only — kept if Path B uses it for raw mode).
  - `_render_executive_posture_via_engine` backwards-compat shim at
    `render_report.py:4225`.
- Replace `render()` body with a thin shim that builds a wrapper and
  calls `_render_via_engine` (now guaranteed for wrapper inputs).

### M4.3 File-size and structure

- Measure `render_report.py` after deletion. Target: under 1500 lines.
  No fixed gate — this repo has none.
- If `load_report_input`, `resolve_options`, `compose_wrapper` together
  are over half the residual file, split into `wrapper_loader.py`
  (input normalization) leaving `render_report.py` as argparse + entry
  + `_render_via_engine` glue.
- Resolves **[REVIEW finding]** on cargo-culted 500-line gate.

### M4.4 CSS consolidation

- Delete dead `fleet-*` selectors from
  `report_engine/templates/_styles.css`. Use the correct primitive:

  ```bash
  for sel in $(awk -F'[ ,{]' '/^\.[a-z]/{print $1}' templates/_styles.css | sort -u); do
    grep -q -r "$sel" templates/ || echo "DEAD: $sel"
  done
  ```

  (V1's `grep -rL` reports files lacking a selector, which is the
  opposite. **Resolves [LOW] from v1 review.**)
- Visually diff a representative fixture render before/after CSS
  changes; capture in PR description.

### M4.5 Retire the parity suites — keep the semantic invariants

**Critical change from v1 and v2:** do **not** downgrade to a
class-presence audit alone. Class checks prove styling scaffolding
exists; they don't prove report *content* stayed correct. Carry the
semantic parity invariants forward as engine-only regression tests.
**Resolves [REVIEW v2 MEDIUM-4]**.

- Delete `tests/test_html_parity.py` and `tests/test_markdown_parity.py`.
  They diff legacy vs. engine; with legacy gone, both paths collapse
  into one.
- Replace with **two** new permanent test files:
  - `tests/test_report_class_audit.py`: per-report-type expected-class
    matrix as a test fixture; asserts the styling scaffolding
    (`narrative-slot`, `gauge-card`, `report-purpose`, `prose`, plus
    type-specific like `landscape-grid` for fleet reports). Lightweight
    smoke test.
  - `tests/test_report_semantics.py`: engine-only version of the M2/M3
    semantic invariants — heading set + order, keyed table rows (by
    entity_type + identifier), warning text presence, note placement
    in slot, numeric values keyed by row+column, byte envelope per
    report type. Uses the same `tests/_html_tree.py` helper.
  - Together they replace the parity oracle without losing semantic
    coverage.

### M4 verification gate

- Full test suite green: `python3 -m unittest discover -s tests`,
  `python3 -m pytest tests/`.
- `tests/test_report_class_audit.py` green for all 7 report types,
  both formats.
- Manual 12-invocation smoke matrix against demo.trafficpeak.live and
  acme — visual parity with `/private/tmp/check-fleet.html`,
  SOC graceful fallback and `edge_ops_impact --include-paths` graceful
  fallback both still fire on stderr.
- File-size envelope per report type measured and noted in PR.

### M4 rollback

`git revert` the M4 merge commit. Legacy code returns. HTML and Markdown
both still flow through the engine (M2/M3 stays in place). The reverted
deletion is fully reversible because no schema or interface changed.

---

## Cross-milestone rollback envelope

```
M4 reverted → engine path intact (M3 still active), legacy code restored.
M3 reverted → Markdown reverts to legacy; HTML routing via engine intact.
M2 reverted → HTML routing reverts to v1 partial state; legacy intact.
M1 reverted → control_review stub restored; no live impact (control_review never routed).
```

Each milestone is independently revertable because each one preserves
the prior milestone's contract. No milestone deletes a code path that
later milestones depend on.

## Validation commands (corrected for this repo)

Replace every v1 `uv run pytest` reference with one of:

| Purpose | Command |
|---------|---------|
| CI test suite (canonical) | `python3 -m unittest discover -s tests` |
| pytest run (parameter-expanded count) | `python3 -m pytest tests/` |
| skill examples validation | `python3 scripts/validate-skill-examples.py` |
| targeted | `python3 -m pytest tests/test_report_engine.py tests/test_skill_scripts.py` |

There is no `make pre-push`, no commit gate, and no `uv` in this repo.

## Critical files (revised)

| Milestone | File | Purpose |
|-----------|------|---------|
| M1.1 | `report_engine/humanize.py` (MOD) | Absorb `rule_label_parts`, `METRIC_LABELS`, `human_metric_name` |
| M1.1 | `report_engine/deltas.py` (new) | Canonical `pct_delta`, `signed_delta_pp` |
| M1.1 | `report_engine/contexts/executive_posture.py` (MOD) | Import shared `METRIC_LABELS` |
| M1.1 | `report_engine/contexts/scorecard_brief.py` (MOD) | Import shared `METRIC_LABELS` |
| M1.1 | `render_report.py` (MOD) | Re-export moved utilities |
| M1.2 | `report_engine/contexts/_shared.py` (MOD) | Add `select_control_companions` + helpers |
| M1.2 | `report_engine/contexts/control_review.py` (rewrite, ~750–900 lines) | `assemble`, `prepare`, `NOTE_ID_TO_SLOT` |
| M1.2 | `report_engine/templates/reports/control_review.html` (new) | Control review HTML template |
| M1.2 | `report_engine/templates/macros/control_bars.html` (new) | Before/after/expected SVG |
| M1.2 | `tests/test_report_engine.py` (MOD) | `select_control_companions` unit tests + `test_control_review_*` |
| M1.3 | `report_engine/templates/` (MOD) | `:.0%`/`:.2f` → filters |
| M2.1 | `tests/_html_tree.py` (new, helper) | stdlib `html.parser`-backed tree-builder for tests |
| M2.1 | `tests/test_html_tree.py` (new) | Unit tests for the tree-builder helper |
| M2.1 | `tests/test_html_parity.py` (new, durable until M4) | Keyed semantic parity gate using `_html_tree` |
| M2.1 | `tests/fixtures/parity_allowlist.json` (new) | Class-rename allowlist with justifications |
| M2.3 | `render_report.py` (MOD) | Route all 7 types through engine for HTML |
| M3.1 | `report_engine/templates/reports/*.md.j2` (new × 7) | Markdown templates |
| M3.1 | `report_engine/markdown.py` (MOD) | `md_escape` filter |
| M3.1 | `report_engine/render.py` (MOD) | `fmt`-aware template selection, autoescape policy |
| M3.2 | `tests/test_markdown_parity.py` (new, durable until M4) | DOM/structural Markdown parity |
| M3.3 | `render_report.py` (MOD) | Route Markdown through engine for all 7 types |
| M4.2 | `render_report.py` (MOD, large deletion) | Remove `html_*`, `md_*`, dispatcher, etc. |
| M4.3 | `wrapper_loader.py` (new, conditional) | Split if residual file is unwieldy |
| M4.4 | `report_engine/templates/_styles.css` (MOD) | Drop dead selectors |
| M4.5 | `tests/test_report_class_audit.py` (new) | Permanent class-presence audit |
| M4.5 | `tests/test_report_semantics.py` (new) | Engine-only carry-forward of semantic parity invariants |

## Out of scope (unchanged)

- Refactoring `bot_insights_report.py` orchestration.
- Changing `bot_report_input.v1` schema or `schema_version` constants.
- Rewriting chart primitives in `report_engine/charts.py`.
- Modifying `scripts/baselines.py`.
- Adding new macros for slot types not exercised by the oracle.

## Verification summary

1. **Per-milestone tests** as listed above, with `unittest discover` and
   `pytest` as the validation commands.
2. **Durable regression suites** in M2 and M3 (the parity gates) plus
   the permanent class-name audit in M4.
3. **Rollback envelope** documented per-milestone.
4. **Manual smoke gate** lives in M4 only — once everything is routed
   through the engine and legacy is gone.
5. **Reused functions preserved**: `pct2`, `signed_pct`, `pct_delta`,
   `direction`, `score_gauge_svg`, `score_bar_svg`, `ENTITY_TYPE_LABELS`
   continue to exist; `rule_label_parts`, `human_metric_name`,
   `METRIC_LABELS` move to `report_engine/humanize.py`.

## M4.1 decision record (Path B confirmed)

**Decision (2026-05-10):** M4 proceeds under **Path B (preserve
raw-artifact mode)**. No named telemetry source was identified and
signed off ahead of M4 to justify Path A's retirement of raw-mode,
which is the pre-approval gate the plan v3 trailer requires
([REVIEW v2 MEDIUM-3]).

Consequences carried through the rest of M4:

- `markdown_to_simple_html()`, the `is_wrapper` short-circuit, the
  inline `<style>` heredoc, and the `html_*`/`md_*`/`render_html`/
  `render_markdown` dispatch tree all stay reachable for raw-mode
  inputs.
- The wrapper-only deletion list narrows to: the
  `_render_executive_posture_via_engine` backwards-compat shim
  (M2.3 left it as a name-only fallback; no caller depends on it)
  and the parity-gate consumers (`tests/test_html_parity.py`,
  `tests/test_markdown_parity.py`).
- The `BOT_INSIGHTS_RENDER_PATH` env override **survives** as test
  infrastructure for the ~28 wrapper-mode legacy regression tests
  in `BotInsightsScriptTests` (legacy markdown sections, legacy
  HTML chart titles, ``<h2>Analyst Notes</h2>``, etc.). The override
  itself is a few lines; the legacy code it gates is preserved for
  raw-mode in any case under Path B, so the override costs nothing
  extra. Rewriting those 28 tests against engine output is a
  separate, mechanically-mostly-trivial PR — out of scope for this
  M4. The class-level ``setUpClass`` pin documents this carve-out.
- M4.5 semantic-test carry-forward
  (`tests/test_report_semantics.py` + `tests/test_report_class_audit.py`)
  lands **before** the parity retirement so the engine path stays
  covered without a regression window.
- Path A remains revisitable: a future PR with a named telemetry
  source attesting to zero raw-mode callers in a stated time window
  could delete the surviving raw-mode helpers, the
  `markdown_to_simple_html()` regex builder, and the inline
  `<style>` heredoc. This M4 leaves the wiring intact.

## M4.3 / M4.4 audit notes

**M4.3 file size:** `render_report.py` post-M4 = 4287 lines. The
plan v3 1500-line target assumed Path A; under Path B the legacy
`html_*` / `md_*` / `render_html` / `render_markdown` /
`markdown_to_simple_html` / inline-`<style>` surface stays
reachable for raw-mode (and the carved-out wrapper-mode legacy
regression tests). `load_report_input` + `resolve_options` together
are well under half the residual file (~150 lines combined), so no
`wrapper_loader.py` split.

**M4.4 dead CSS:** zero `fleet-*` selectors remain in
`_styles.css` (M2.3 already cleaned them up); the corrected
`awk | grep -q -r` primitive run against the broader stylesheet
produces a candidate list of ~12 selectors that are emitted only by
the inline `<style>` heredoc itself, not by any template or by
``charts.py``-generated SVG (e.g. `gauge-card`, `gauge-caption`,
`finding:last-child`, `sec-evidence-block:first-child`). Deleting
them would change the inline `<style>` content embedded in every
rendered HTML snapshot, requiring a snapshot refresh across
~14 fixtures. The behavioral impact is zero; the snapshot churn is
the only signal. Deferred to a follow-up "CSS pruning + snapshot
refresh" PR explicitly scoped as cosmetic so the diff is
self-contained.

## Changes from v2 (round-2 codex review tightening)

1. **Parser decision named:** stdlib `html.parser` with a small
   `tests/_html_tree.py` tree-builder helper. No third-party deps. No
   regex fallback. Generation still uses Jinja2 unchanged.
2. **Numeric parity is keyed**, not set-membership: every percentage
   and delta is asserted against a (heading, table caption, row label,
   column header) tuple. Catches "right number, wrong cell" bugs.
3. **HTML parity refresh in M3** when `render.py` becomes `fmt`-aware:
   update `tests/test_html_parity.py` call sites and re-run to confirm
   no regression introduced by the Markdown plumbing change.
4. **M4.1 default is Path B (preserve raw-artifact mode).** Path A
   requires a named telemetry source pre-approval. No mid-milestone
   ad hoc audits trigger retirement.
5. **M4.5 carries semantic invariants forward**: `tests/test_report_semantics.py`
   (engine-only) plus `tests/test_report_class_audit.py`. Class
   presence alone is too weak.

## Changes from v1 (for review)

1. **Scope clarified:** wrapper mode only. Raw-artifact migration is a
   follow-up. (Was [HIGH-1] contradiction.)
2. **Split into 4 PRs** with green CI at each boundary.
3. **Parity gate is durable**, not throwaway. Lives across M2/M3 then
   is replaced by a class-presence audit in M4. (Was [HIGH-3] gate
   non-durability.)
4. **HTML and Markdown have separate parity suites and separate
   forcing milestones.** (Was [HIGH-2] / [MEDIUM-1] markdown
   uncovered.)
5. **Rollback** documented per milestone.
6. **Validation commands** corrected for this repo (no `uv`, no
   commit gate).
7. **Fixture-class handling** (wrapper / raw / examples /
   expected-failure) spelled out. (Was [MEDIUM-2].)
8. **.md.j2 wiring** (template selection, autoescape, escape filter)
   spelled out. (Was [MEDIUM-3].)
9. **control_review size estimate** revised to ~750–900 lines.
10. **`select_control_companions` unit tests** required before legacy
    deletion. (Was [MEDIUM-5] missing tests.)
11. **Dead-CSS detection primitive** corrected. (Was [LOW] from v1.)
12. **Legacy snapshot story** resolved: parity suites *are* the
    durable oracle from M2 onward; M4 replaces them with a class
    audit.
13. **Fact corrections**: `render_report.py` is 4336 lines (not
    ~1000), there are 20 `md_*` (not 13), and only 4 of 7 HTML report
    types currently route through the engine (v1 implied all 7 except
    control_review did).
