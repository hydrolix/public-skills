# crawler_governance render parity

> Plan. Branch: `crawler-governance-render`. Worktree:
> `.worktrees/crawler-governance-render`. Builds on the (uncommitted)
> `crawler-governance` capture-orchestration branch — that branch ships
> first; this one promotes the rendered output of the same report type
> into the `report_engine/` Jinja path so it matches the visual style of
> `executive_posture`, `soc_triage`, and `scorecard_brief`.

## Context

`bot_insights_report.py --report crawler_governance` now produces a wrapper
whose HTML rendering goes through the **legacy markdown→HTML** path in
`render_report.py` (the `crawler_governance` branch at lines 1393–1398
calls `md_domain_report("Crawler Governance", …, crawler_features_for_card)`).
The legacy path emits the bare structure visible at
`reports/bot-insights-examples/crawler-governance.md`:

- a single "Crawler Governance Summary" table
- a flat per-entity "Crawler Governance Evidence" feature list
- a verbose "Evidence Limits" schema dump
- a "Warnings" tail

`executive_posture` and `soc_triage` are routed through the
`_render_via_engine` shim at `render_report.py:4272`, which loads a
report-specific context module from
`skills/bot-insights/scripts/report_engine/contexts/` and renders a
Jinja template under `report_engine/templates/reports/`. The engine
output is what `/private/tmp/wrap-review/brief-fleet-after-followups.html`
looks like — kicker / dek / window strip, executive summary slot, score
landscape, verdict strip, KPI strip, headline findings, queue table,
recommended next steps, coverage detail, method block, orientation
disclosure.

This plan adds the same engine path for `crawler_governance` — one
unified template, no `*_entity_review` split for now (the
`scorecard_brief` family has both `scorecard_brief.html` and
`scorecard_entity_review.html`; if a single-entity lens for crawler is
needed later, it can be extracted from this template, mirroring the
existing precedent).

## What changes

### 1. `report_engine/contexts/crawler_governance.py` (NEW)

Modeled on `report_engine/contexts/soc_triage.py`. Exposes the standard
context-module surface:

```python
SCHEMA = "bot_scorecard_artifacts.v1"
REPORT_TYPE = "crawler_governance"
TEMPLATE = "reports/crawler_governance.html"
NOTE_ID_TO_SLOT = {
    "llm-interpretation": "executive_summary",
    "llm-operational": "operational_interpretation",
    "llm-finding-overrides": "finding_overrides",
}
PURPOSE = {
    "kicker": "Bot Insights — crawler governance",
    "measures": (
        "A health score for each ranked crawler entity (AI category, bot "
        "class, or request host) on a 0–100 scale. Higher scores reflect "
        "more triggered crawler-governance signals — good-bot 429 / error "
        "rate, AI-crawler growth, governance surface failures — plus rate "
        "delta context when the rowset population is crawler-specific."
    ),
    "score_legend": (
        "Higher score = more triggered crawler-governance rules. "
        "Bands: escalate, monitor, observe."
    ),
    "cant_say": (
        "Not a confirmed-malicious-crawler call. Missing inputs are "
        "reported as missing — they are not scored as safe."
    ),
}
```

`assemble(artifacts)` — same dual-path the SOC module uses:

- Bundled `bot_scorecard_artifacts.v1` packet → unpack to `index` +
  `scorecards` + `producer_limit` + `result_truncated` +
  `total_ranked_entities`.
- Flat `bot_scorecard_index.v1` + list of `bot_entity_scorecard.v1` →
  same fields, with `total_ranked` falling back to
  `len(index.ranked_entities)`.

Either shape with no index raises
`ValueError("crawler_governance wrapper missing bot_scorecard_index.v1 artifact")`.

`prepare(artifact)` — produces the template context. Reuses the
`scorecard_brief._entity_row` and `_aggregate_actions` helpers (the SOC
context already imports both); reuses
`scorecards_mod.rule_counts` and `verdicts_mod.classify` for per-entity
verdicts; reuses the `Counter`/confidence-reasons aggregation pattern
from SOC.

The crawler context departs from SOC in three ways:

1. **Headline lead clause** — picks from crawler features in this order
   (analogous to `_SECURITY_RULE_ORDER` in SOC):
   ```python
   _CRAWLER_RULE_ORDER = (
       "policy_surface_failure_present",
       "good_bot_429_present",
       "good_bot_error_rate_high",
       "ai_crawler_growth_high",
       "rate_429_delta_high",
       "rate_5xx_delta_high",
   )
   ```
   When the top entity's primary domain is `crawler_governance`, the
   headline finding is built from the highest-priority triggered rule
   on that entity. If the top entity's primary domain is something
   else (e.g. `movement` outranks crawler), the headline falls back to
   the SOC-style "actionable summary" pattern.

2. **Coverage lens** — coverage table is filtered to the
   `crawler_governance` domain (plus any domain that contributed to
   any entity's score); SOC includes only `security_evidence`, but
   crawler reports often surface secondary-domain features
   (`movement.volume_delta_high`, `cache_busting.cache_miss_rate_high`)
   when the producer ranked on `request_host`. Show those secondary
   domains in the coverage table, keep the spotlight on
   `crawler_governance`.

3. **Per-entity evidence card** — the `security_evidence_cards.html`
   macro is SOC-specific (its labels and per-rule callouts assume
   bad-bot/SIEM evidence). The crawler context emits a parallel
   `crawler_evidence_cards` payload and the new template uses the
   generic `embedded_scorecards.html` macro (which already exists at
   `templates/macros/embedded_scorecards.html`) plus a thin
   crawler-specific section for the four `crawler_governance`-only
   features. **Don't** duplicate the security cards macro for
   crawler; if the existing
   `embedded_scorecards.html` shape is adequate, use it as-is. If it
   needs a small affordance (e.g. a "primary feature" callout per
   card), add an optional kwarg to the existing macro rather than
   forking it.

### 2. `report_engine/templates/reports/crawler_governance.html` (NEW)

Modeled on `report_engine/templates/reports/soc_triage.html`. Reuses,
in order:

- `{% from "macros/triage_strip.html" import triage_strip %}`
- `{% from "macros/executive_summary.html" import executive_summary %}`
- `{% from "macros/queue_table.html" import queue_table %}`
- `{% from "macros/embedded_scorecards.html" import embedded_scorecards %}` (instead of `security_evidence_cards`)
- `{% from "macros/domain_score_matrix.html" import domain_score_matrix %}`
- `{% from "macros/coverage_table.html" import coverage_table %}`
- `{% from "macros/method.html" import method_section %}`
- `{% from "macros/report_purpose.html" import report_purpose %}`

Block layout, top to bottom:

1. `hero` block — `executive_summary` macro with the headline finding
   and analyst-note interpretation slot (same as SOC).
2. `content` block:
   - Optional `degraded` banner (when wrapper has only the index).
   - `triage_strip` (Watch/Assign/Insufficient/Close pills).
   - `embedded_scorecards` for the per-entity crawler-governance
     evidence cards.
   - `queue_table` ranked by primary-domain score, unit label
     `entity_type_label` (resolved from the index;
     `humanize_entity_type("ai_category") == "AI category"`,
     `bot_class → "bot class"`, `request_host → "request host"`).
   - `domain_score_matrix` so the analyst can see where the points
     landed across all evaluated domains, not just crawler.
   - "Recommended next steps" section — copy SOC's pattern (deduped
     across entities by detail text, with affected-entity preview).
   - `coverage_table` — rule coverage by domain.
   - `method_section` with schema, table, comparison, producer limit.
   - `report_purpose` collapsible orientation disclosure.

### 3. `report_engine/contexts/__init__.py` (MOD)

Add `crawler_governance` to the imports and `_MODULES` tuple. The
existing `SCHEMA_REGISTRY` filter (`if mod.REPORT_TYPE != "soc_triage"`)
is there because SOC and `scorecard_brief` share the
`bot_scorecard_artifacts.v1` schema and `scorecard_brief` wins the
schema-mode default. `crawler_governance` shares the same schema and
needs the same exclusion — extend the filter to a set:

```python
_SCHEMA_REGISTRY_EXCLUSIONS = {"soc_triage", "crawler_governance"}
SCHEMA_REGISTRY = {
    mod.SCHEMA: mod
    for mod in _MODULES
    if mod.REPORT_TYPE not in _SCHEMA_REGISTRY_EXCLUSIONS
}
```

`REPORT_TYPE_REGISTRY` picks crawler up automatically because it iterates
over `_MODULES`.

### 4. `render_report.py` (MOD)

Extend the engine-route condition at line 4272:

```python
if report_type in {"executive_posture", "soc_triage", "crawler_governance"}:
    engine_html = _render_via_engine(...)
```

The legacy `md_domain_report("Crawler Governance", …)` markdown branch
at line 1393 stays as-is — markdown callers (`--format markdown`)
continue to use it. Only the HTML route flips to the engine.

### 5. Tests

Three new tests in `tests/test_report_engine.py`, modeled exactly on
`test_soc_triage_full_wrapper` / `_index_only_degraded` / `_single_entity`:

- `test_crawler_governance_full_wrapper` — fixture
  `tests/fixtures/report_engine/crawler_governance_full.json` (a
  three-entity `bot_scorecard_artifacts.v1` packet with an
  `ai_category`-ranked index, all six crawler features triggered on
  the top entity), snapshot
  `tests/snapshots/report_engine/crawler_governance_full.html`.
- `test_crawler_governance_index_only_degraded` — fixture with the
  index but no scorecard cards (degraded mode), snapshot.
- `test_crawler_governance_single_entity` — single-entity wrapper,
  snapshot.

Snapshots are regenerated with `UPDATE_SNAPSHOTS=1 pytest …` (the
existing snapshot helpers in `tests/test_report_engine.py` already
handle this).

The orchestration tests added on the `crawler-governance` branch keep
passing untouched — this branch only changes the renderer, not the
producer.

### 6. Example regeneration

`reports/bot-insights-examples/crawler-governance.html` (and `.md`) are
checked in. Re-render them from the existing wrapper at
`skills/bot-insights/examples/crawler-governance.json`:

```
uv run python skills/bot-insights/scripts/render_report.py \
  --file skills/bot-insights/examples/crawler-governance.json \
  --format html \
  --output reports/bot-insights-examples/crawler-governance.html
uv run python skills/bot-insights/scripts/render_report.py \
  --file skills/bot-insights/examples/crawler-governance.json \
  --format markdown \
  --output reports/bot-insights-examples/crawler-governance.md
```

The HTML file should now contain the rich `report_engine/` markup
(verdict strip, kicker, dek, queue table, etc.). The Markdown file
stays on the legacy path and is unchanged.

## Critical files

| File | Purpose |
|------|---------|
| `skills/bot-insights/scripts/report_engine/contexts/crawler_governance.py` (NEW) | `assemble`/`prepare`/constants for the crawler engine path. |
| `skills/bot-insights/scripts/report_engine/templates/reports/crawler_governance.html` (NEW) | Jinja template reusing the existing macros. |
| `skills/bot-insights/scripts/report_engine/contexts/__init__.py` (MOD) | Register the new module; broaden the schema-registry exclusion to a set. |
| `skills/bot-insights/scripts/render_report.py` (MOD) | Add `crawler_governance` to the engine-route condition at line 4272. |
| `tests/fixtures/report_engine/crawler_governance_full.json` (NEW) | Three-entity wrapper fixture for the snapshot test. |
| `tests/fixtures/report_engine/crawler_governance_index_only.json` (NEW) | Degraded-mode fixture. |
| `tests/fixtures/report_engine/crawler_governance_single_entity.json` (NEW) | Single-entity fixture. |
| `tests/snapshots/report_engine/crawler_governance_*.html` (NEW) | Generated snapshots for the three fixtures. |
| `tests/test_report_engine.py` (MOD) | Three test methods modeled on the SOC tests. |
| `reports/bot-insights-examples/crawler-governance.html` (REGEN) | Re-rendered from the existing example wrapper. |

## Reused functions and conventions

The new context module reuses, not re-implements:

- `humanize.{cluster_display, humanize_entity_type, humanize_identifier}`
- `theme.{DOMAIN_LABELS, DOMAIN_ORDER}`
- `scorecards.rule_counts`
- `verdicts.classify`
- `findings.Finding`
- `formatters.format_share_pct`
- `scorecard_brief._entity_row`, `scorecard_brief._aggregate_actions`
  (already imported by `soc_triage.py` — the same imports are
  appropriate here)

The new template reuses, not re-implements: every macro listed under
section 2.

## Out of scope

- A separate `crawler_governance_entity_review` context for a
  single-entity deep-dive lens. Mirror of `scorecard_entity_review`
  is deferred until a customer use case calls for it; the unified
  template handles single-entity wrappers correctly via the queue
  table's `n_total == 1` path (the SOC suite covers that path with
  `test_soc_triage_single_entity`).
- Changes to the legacy markdown renderer for crawler — the legacy
  path stays intact for `--format markdown` and the `md_*_report`
  callsites already exercised by tests.
- New crawler-governance feature evaluators in `scorecard.py`. The
  six existing evaluators are what this template renders against.
- Promoting `edge_ops_impact` to the engine path. Tracked separately;
  it has more moving parts (path-grain artifacts, mixed schemas) and
  deserves its own plan.
- The `purpose-strip` color key in `report_purpose.html` — the macro
  is already shared and the existing `palette` shape (`escalate`,
  `monitor`, `observe`) covers crawler bands without modification.

## Verification

- `uv run pytest tests/test_report_engine.py -k crawler_governance` —
  three new snapshot tests pass on first generation, deterministic
  on rerun.
- `uv run pytest tests/test_report_engine.py tests/test_skill_scripts.py`
  — full suite green; the orchestration tests from the prior branch
  still pass.
- `uv run ruff format skills/bot-insights/scripts/report_engine/contexts/crawler_governance.py skills/bot-insights/scripts/report_engine/templates/reports/crawler_governance.html skills/bot-insights/scripts/report_engine/contexts/__init__.py skills/bot-insights/scripts/render_report.py tests/test_report_engine.py`
- `uv run ruff check --fix` on the same set.
- `uv run mypy skills/bot-insights/scripts/report_engine/contexts/crawler_governance.py skills/bot-insights/scripts/render_report.py` — no errors above the baseline established on `main`.
- Visual check: the regenerated
  `reports/bot-insights-examples/crawler-governance.html` shows the
  same structural sections as
  `/private/tmp/wrap-review/brief-fleet-after-followups.html` — kicker,
  dek, window comparison, executive summary, score landscape (where
  applicable), verdict strip, KPI strip, headline findings, queue
  table, recommended next steps, coverage detail, method, orientation.
- Spot-check by reading the regenerated HTML cold — the report should
  be self-explanatory at a director-of-engineering reading level
  (the `review-prompt.md` test): headline tells you whether it
  matters, "What triggered" tells you what changed, units are
  presented correctly, and analyst-grade detail is gated behind a
  disclosure rather than at the top of the report.
