# edge_ops_impact engine port

> Plan. Branch: `edge-ops-impact-render`. Worktree:
> `.worktrees/edge-ops-impact-render`. Promotes the rendered output of
> the `edge_ops_impact` report type into the `report_engine/` Jinja
> path, matching the visual style of `executive_posture`, `soc_triage`,
> and `crawler_governance`. Adds a path-grain "Top paths" section
> driven by the existing `cache_origin_impact_report.v1` detector
> output when present in the wrapper's `artifacts[]`.

## Context

`bot_insights_report.py --report edge_ops_impact` (capture orchestration
not yet implemented for this report type) and direct calls to
`render_report.py` with an `edge_ops_impact` wrapper currently route
HTML rendering through the **legacy markdown→HTML** path. The legacy
branch at `render_report.py:1399–1404` calls
`md_domain_report("Edge/Ops Impact", selected, limit, ctx, edge_ops_features_for_card)`,
which emits the same flat shape we saw for crawler before its engine
port: a single summary table, a per-entity feature list, an evidence-
limits dump, and a warnings tail.

`crawler_governance` and `soc_triage` are routed through
`_render_via_engine` at `render_report.py:4274`. Each loads a context
module from `report_engine/contexts/` and renders a Jinja template
under `report_engine/templates/reports/`. The engine output carries
kicker, dek, window strip, executive summary, queue table, evidence
cards, domain matrix, recommended next steps, coverage detail, method
block, and orientation disclosure.

This plan adds the same engine path for `edge_ops_impact`, with two
additions over the crawler port:

1. **Cost-share headline lens.** When every actionable entity carries
   `origin_cost_contribution_pct` (already an emitted feature input on
   the `origin_cost_contribution_high` rule), the lead clause reads in
   cost-share units (`Top {N} entities concentrate {pct}% of origin
   pressure`). When that input is missing on any actionable entity, the
   headline falls back to the highest-priority triggered rule, mirroring
   the crawler context's `_crawler_lead_clause` shape.
2. **Top paths section.** When the wrapper's `artifacts[]` includes a
   `cache_origin_impact_report.v1` artifact (the path-grain detector
   output produced by `skills/bot-insights/scripts/cache_origin_impact.py`),
   the template renders a "Top paths" section ranking path-grain
   candidates by `candidate_score`. When that artifact is absent, the
   section is omitted; the rest of the report renders unchanged.

The legacy markdown renderer for `edge_ops_impact` stays intact —
`--format markdown` callers continue to use `md_domain_report`. Only
the HTML route flips to the engine.

## What changes

### 1. `report_engine/contexts/edge_ops_impact.py` (NEW)

Modeled on `report_engine/contexts/crawler_governance.py`. Exposes the
standard context-module surface:

```python
SCHEMA = "bot_scorecard_artifacts.v1"
REPORT_TYPE = "edge_ops_impact"
TEMPLATE = "reports/edge_ops_impact.html"
NOTE_ID_TO_SLOT = {
    "llm-interpretation": "executive_summary",
    "llm-operational": "operational_interpretation",
    "llm-finding-overrides": "finding_overrides",
}
PURPOSE = {
    "kicker": "Bot Insights — edge & origin cost",
    "measures": (
        "A cost-impact score for each ranked entity (ASN, host, or "
        "bot class) on a 0–100 scale. Higher scores reflect more "
        "triggered cache-busting and origin-impact signals — cache-miss "
        "rate / delta, query-string diversity, origin p95 delta, "
        "origin-cost contribution share."
    ),
    "score_legend": (
        "Higher score = more triggered edge/origin rules. "
        "Bands: escalate, monitor, observe."
    ),
    "cant_say": (
        "Origin cost is reported as a percentage share, not a billing "
        "figure. Missing inputs are reported as missing — they are "
        "not scored as zero cost."
    ),
}

_EDGE_RULE_ORDER = (
    "origin_cost_contribution_high",
    "origin_p95_delta_high",
    "cache_miss_rate_high",
    "cache_miss_delta_high",
    "querystring_diversity_with_high_miss_rate",
    "querystring_diversity_high",
)
```

`assemble(artifacts)` — same dual-path the SOC/crawler modules use,
plus a third pickup for path-grain:

- Bundled `bot_scorecard_artifacts.v1` packet → `index` + `scorecards`
  + `producer_limit` + `result_truncated` + `total_ranked_entities`
- Flat `bot_scorecard_index.v1` + list of `bot_entity_scorecard.v1` →
  same fields, with `total_ranked` falling back to
  `len(index.ranked_entities)`
- **NEW**: `cache_origin_impact_report.v1` artifact (zero or one
  occurrence) → `path_report` field on the assembled dict. When
  multiple instances appear, take the first and surface a warning via
  the renderer's `ctx.warnings` (mirroring the de-dup behavior in
  `dedupe_artifact_bodies`); the assemble step itself only takes the
  first. When zero, `path_report = None`.

Either shape with no scorecard index raises
`ValueError("edge_ops_impact wrapper missing bot_scorecard_index.v1 artifact")`
— path-grain alone is not sufficient; v1 is entity-grain plus optional
path supplement.

`prepare(artifact)` — produces the template context. Reuses
`scorecard_brief._entity_row` and `_aggregate_actions` (the SOC and
crawler contexts already import both); reuses `scorecards_mod.rule_counts`
and `verdicts_mod.classify` for per-entity verdicts; reuses the
`Counter`/confidence-reasons aggregation pattern from SOC/crawler.

The edge_ops_impact context departs from crawler_governance in three
places:

1. **Headline lead clause** — `_edge_lead_clause(sc, scorecards)`:
   - If every actionable scorecard carries
     `origin_cost_contribution_pct` (look on
     `origin_cost_contribution_high` feature's `current` or
     `supporting_metrics.cost_share_pct`), compute the sum across
     the top-N actionable entities and emit
     `"top {n_assign} entities concentrate {pct}% of origin pressure"`.
   - Otherwise fall through to the highest-priority triggered rule
     using `_EDGE_RULE_ORDER` (mirror `_CRAWLER_RULE_ORDER` shape).
   - When path candidates exist, append
     `"; top path {primary_dim_value} carries {miss_share}% of cache misses"`
     where `miss_share` comes from `path_candidates[0].current.cache_miss_pct`
     or from `share_denominators` if present.

2. **Coverage lens** — coverage table leads with `cache_busting` and
   `origin_impact`, then includes any other domain that contributed to
   any entity's score (mirror the crawler context's filter, but with
   the edge domains as the lead pair).

3. **Path-grain rendering** — emits `path_candidates` list:

   ```python
   [
       {
           "rank": cand["rank"],
           "dimensions": cand["entity"],          # dict like {"request_path_norm": "/x"}
           "primary_label": _path_primary_label(cand["entity"]),
           "score": cand["candidate_score"],
           "band": cand["candidate_band"],
           "confidence": cand["confidence"],
           "current": cand["current"],
           "baseline": cand["baseline"],
           "deltas": cand["deltas"],
           "finding_types": cand["finding_types"],
           "miss_share_pct": _miss_share(cand),    # cache_miss share among requests
           "origin_share_pct": _origin_share(cand),
           "evidence": _path_evidence_line(cand),  # one short string for the row footer
       }
       for cand in path_report["candidates"][:limit]
   ]
   ```

   `_path_primary_label({"request_path_norm": "/x", "bot_class": "y"})`
   joins dimension values with " · " and bolds the path. When path_report
   is absent, `path_candidates` is `[]` and the template suppresses the
   section.

The crawler's `_crawler_evidence_cards` shape is reused with a renamed
emission as `_edge_evidence_cards` — block label "Edge & origin
signals" instead of "Crawler-governance signals", domain filter
`{"cache_busting", "origin_impact"}` instead of `{"crawler_governance"}`.
Don't fork the underlying card shape — the template inlines the same
`<article class="sec-evidence-card">` markup we used for crawler.

### 2. `report_engine/templates/reports/edge_ops_impact.html` (NEW)

Modeled on `report_engine/templates/reports/crawler_governance.html`.
Reuses, in order:

- `{% from "macros/triage_strip.html" import triage_strip %}`
- `{% from "macros/executive_summary.html" import executive_summary %}`
- `{% from "macros/queue_table.html" import queue_table %}`
- `{% from "macros/embedded_scorecards.html" import embedded_scorecards %}`
- `{% from "macros/domain_score_matrix.html" import domain_score_matrix %}`
- `{% from "macros/coverage_table.html" import coverage_table %}`
- `{% from "macros/method.html" import method_section %}`
- `{% from "macros/report_purpose.html" import report_purpose %}`

Block layout, top to bottom (delta from `crawler_governance.html`
called out **bold**):

1. `hero` block — `executive_summary` macro with the actionable Finding.
2. `content` block:
   - Optional `degraded` banner (when wrapper has only the index).
   - `triage_strip` (Watch/Assign/Insufficient/Close pills).
   - **Edge & origin evidence cards** — same `<article class="sec-evidence-card">`
     markup the crawler template uses, with block label "Edge & origin
     signals" and the `c.crawler_features` payload renamed to
     `c.edge_features`. Render only when `edge_cards` is non-empty
     (i.e., not in degraded mode).
   - `queue_table` ranked by primary-domain score.
   - **NEW: Top paths section.** Renders only when `path_candidates`
     is non-empty:

     ```jinja
     {% if path_candidates %}
       <section class="path-candidates-section">
         <div class="section-eyebrow">Top paths</div>
         <h2>{{ path_candidates | length }} path candidate{{ "s" if path_candidates | length != 1 else "" }} ranked by combined cache-miss and origin pressure</h2>
         <table class="data-table path-candidates-table">
           <thead>
             <tr>
               <th>#</th>
               <th>Path</th>
               <th class="num">Cache-miss share</th>
               <th class="num">Origin pressure share</th>
               <th class="num">Score</th>
               <th>Band</th>
               <th>Confidence</th>
             </tr>
           </thead>
           <tbody>
             {% for p in path_candidates %}
               <tr>
                 <td class="num">{{ p.rank }}</td>
                 <td class="entity">{{ p.primary_label }}</td>
                 <td class="num">{{ p.miss_share_pct | pct2 if p.miss_share_pct is not none else "—" }}</td>
                 <td class="num">{{ p.origin_share_pct | pct2 if p.origin_share_pct is not none else "—" }}</td>
                 <td class="num">{{ p.score }}</td>
                 <td>{{ p.band | humanize_band }}</td>
                 <td>{{ p.confidence | humanize_confidence }}</td>
               </tr>
               {% if p.evidence %}
                 <tr class="path-candidates-evidence-row">
                   <td></td>
                   <td colspan="6"><span class="muted">{{ p.evidence | normalize_percents }}</span></td>
                 </tr>
               {% endif %}
             {% endfor %}
           </tbody>
         </table>
       </section>
     {% endif %}
     ```

   - `embedded_scorecards` per-entity rollup.
   - `domain_score_matrix` filtered to domains that scored.
   - "Recommended next steps" — copy SOC/crawler's pattern.
   - `coverage_table` — rule coverage by domain, lead pair
     `cache_busting`, `origin_impact`.
   - `method_section` with schema, table, comparison, producer limit.
   - `report_purpose` collapsible orientation disclosure.

### 3. `report_engine/contexts/__init__.py` (MOD)

Add `edge_ops_impact` to the imports and `_MODULES` tuple. Extend the
schema-registry exclusion set:

```python
_SCHEMA_REGISTRY_EXCLUSIONS = {
    "soc_triage",
    "crawler_governance",
    "edge_ops_impact",
}
```

`REPORT_TYPE_REGISTRY` picks edge up automatically because it iterates
over `_MODULES`.

### 4. `render_report.py` (MOD)

Extend the engine-route condition at line ~4274 (current condition
already includes `executive_posture`, `soc_triage`,
`crawler_governance`):

```python
if report_type in {
    "executive_posture",
    "soc_triage",
    "crawler_governance",
    "edge_ops_impact",
}:
    engine_html = _render_via_engine(...)
```

The legacy `md_domain_report("Edge/Ops Impact", …)` markdown branch at
line 1399 stays as-is.

### 5. Tests

Three new tests in `tests/test_report_engine.py`, modeled exactly on
`test_crawler_governance_full_wrapper` /
`_index_only_degraded` / `_single_entity`:

- `test_edge_ops_impact_full_wrapper` — fixture
  `tests/fixtures/report_engine/edge_ops_impact_full.json`. Three
  entities scorecard packet (ranked on `client_asn`); each top entity
  has `cache_busting` + `origin_impact` features triggered, including
  `origin_cost_contribution_high` with a `current` value so the cost
  lens fires. The wrapper also carries a
  `cache_origin_impact_report.v1` artifact with three path candidates.
  Snapshot at `tests/snapshots/report_engine/edge_ops_impact_full.html`.
  Spot-check assertions: cost-share headline (`"concentrate"` token,
  `"% of origin pressure"`), top-paths section
  (`<table class="data-table path-candidates-table">`), evidence cards
  block label "Edge & origin signals".
- `test_edge_ops_impact_index_only_degraded` — fixture with the index
  but no scorecard cards and no path artifact (degraded mode). Asserts
  degraded banner present, queue table renders entities from the
  index, evidence cards absent, top-paths section absent, domain
  matrix absent.
- `test_edge_ops_impact_single_entity_no_paths` — fixture with one
  entity, full per-rule data, `entity_metrics.current_requests`, and
  no path artifact. Asserts:
  - traffic-share clause (`"covers 100% of fleet requests"`)
  - rule-based fallback headline fires (because we deliberately omit
    `origin_cost_contribution_pct` on this fixture's only triggered
    rule)
  - top-paths section absent

Snapshots are regenerated with `REPORT_ENGINE_UPDATE_SNAPSHOTS=1
pytest …` (existing helpers in `tests/test_report_engine.py` handle
this).

### 6. Example regeneration

`reports/bot-insights-examples/edge-ops-impact.{html,md}` do not exist
yet (no example wrapper either). v1 commits a new
`skills/bot-insights/examples/edge-ops-impact.json` synthesized from
the test fixture (the full one), then renders both formats:

```
uv run --with jinja2 --with markdown-it-py --with bleach \
  python skills/bot-insights/scripts/render_report.py \
  --file skills/bot-insights/examples/edge-ops-impact.json \
  --format html \
  --output reports/bot-insights-examples/edge-ops-impact.html
uv run python skills/bot-insights/scripts/render_report.py \
  --file skills/bot-insights/examples/edge-ops-impact.json \
  --format markdown \
  --output reports/bot-insights-examples/edge-ops-impact.md
```

Synthesizing from the test fixture is a one-time bootstrap: once the
capture-orchestration branch ships, the example becomes the natural
output of `bot_insights_report.py --report edge_ops_impact`. Until
then, the synthesized example exists so the docs site has rendered
output to link to.

## Critical files

| File | Purpose |
|------|---------|
| `skills/bot-insights/scripts/report_engine/contexts/edge_ops_impact.py` (NEW) | `assemble`/`prepare`/constants for the edge engine path. Reuses scorecard_brief + crawler patterns; adds path-grain assembly and the cost-share headline. |
| `skills/bot-insights/scripts/report_engine/templates/reports/edge_ops_impact.html` (NEW) | Jinja template reusing existing macros plus the new path-candidates table block. |
| `skills/bot-insights/scripts/report_engine/contexts/__init__.py` (MOD) | Register the new module; extend the schema-registry exclusion set. |
| `skills/bot-insights/scripts/render_report.py` (MOD) | Add `edge_ops_impact` to the engine-route condition. |
| `tests/fixtures/report_engine/edge_ops_impact_full.json` (NEW) | Three-entity wrapper with bundled scorecard packet plus a `cache_origin_impact_report.v1` artifact. |
| `tests/fixtures/report_engine/edge_ops_impact_index_only.json` (NEW) | Degraded-mode fixture (index only, no scorecards, no path artifact). |
| `tests/fixtures/report_engine/edge_ops_impact_single_entity_no_paths.json` (NEW) | Single-entity wrapper, no path artifact, deliberately omits `origin_cost_contribution_pct` so the rule-based fallback headline fires. |
| `tests/snapshots/report_engine/edge_ops_impact_*.html` (NEW) | Generated snapshots for the three fixtures. |
| `tests/test_report_engine.py` (MOD) | Three new test methods modeled on the crawler tests. |
| `skills/bot-insights/examples/edge-ops-impact.json` (NEW) | Synthesized example wrapper (sourced from the full test fixture). |
| `reports/bot-insights-examples/edge-ops-impact.html` (NEW) | Rendered HTML example via the engine. |
| `reports/bot-insights-examples/edge-ops-impact.md` (NEW) | Rendered markdown example via the legacy path (unchanged route). |

## Reused functions and conventions

The new context module reuses, not re-implements:

- `humanize.{cluster_display, humanize_entity_type, humanize_identifier}`
- `theme.{DOMAIN_LABELS, DOMAIN_ORDER}`
- `scorecards.rule_counts`, `scorecards.normalize_rule_results`
- `verdicts.classify`, `verdicts.confidence_chip`,
  `verdicts.STATE_ORDER`, `verdicts.STATE_LABELS`, `verdicts.STATE_TONE`,
  `verdicts.ESCALATE_BANDS`, `verdicts.MONITOR_BANDS`
- `findings.Finding`
- `formatters.format_share_pct`
- `scorecard_brief._entity_row`, `scorecard_brief._aggregate_actions`
  (already imported by `soc_triage.py` and `crawler_governance.py`)

The new template reuses, not re-implements: every macro listed under
section 2.

The path-grain assemble function reuses the `cache_origin_impact.py`
output schema as-is — no new validation. Whatever the detector emits is
what the template renders, with `None`-safe accessors for optional
fields (`miss_share_pct`, `origin_share_pct`, `evidence`).

## Out of scope

- **Capture orchestration** for `edge_ops_impact`. Wiring SQL builders
  + handoff into `bot_insights_report.py` for cache_busting,
  origin_impact, and the path-grain detector is tracked separately,
  mirroring the `crawler-governance` → `crawler-governance-render`
  branch sequence (orchestration ships in its own branch).
- **Posture / mover artifact rendering inside the engine path.** The
  legacy renderer accepts `bot_posture_movement.v1` and
  `bot_mover_attribution.v1` in the edge_ops_impact wrapper; the engine
  port ignores them in v1. Adding an "Edge fleet trend" strip is a
  follow-up if reader value warrants.
- **A separate `cache_origin_impact` engine context.** The path-grain
  detector's artifact is consumed inside `edge_ops_impact` rather than
  getting its own report_type. If a path-only audience emerges,
  splitting later is straightforward (the `path_candidates` shape is
  already self-contained).
- **Real cost figures (dollars, bytes-served).** Cost is reported as
  a percentage share via `origin_cost_contribution_pct`. Surfacing
  absolute origin bytes would need `response_bytes` evidence which
  isn't reliably present.
- **New cache_busting or origin_impact feature evaluators in
  `scorecard.py`.** The six existing feature evaluators
  (`cache_miss_rate_high`, `cache_miss_delta_high`,
  `querystring_diversity_high`,
  `querystring_diversity_with_high_miss_rate`,
  `origin_p95_delta_high`, `origin_cost_contribution_high`) are what
  this template renders against.
- **Changes to the legacy markdown renderer for edge_ops_impact.** The
  legacy path stays intact for `--format markdown` and the
  `md_domain_report("Edge/Ops Impact", …)` callsite already exercised
  by tests.

## Verification

- `uv run pytest tests/test_report_engine.py -k edge_ops_impact` — three
  new snapshot tests pass on first generation, deterministic on rerun.
- `uv run pytest tests/test_report_engine.py tests/test_skill_scripts.py`
  — full suite green.
- `uv run ruff format` on the touched files.
- `uv run ruff check --fix` on the same set.
- `uv run mypy skills/bot-insights/scripts/report_engine/contexts/edge_ops_impact.py skills/bot-insights/scripts/render_report.py`
  — no errors above the baseline established on `main`.
- Visual check: the rendered
  `reports/bot-insights-examples/edge-ops-impact.html` shows kicker,
  dek, executive summary, triage strip, edge/origin evidence cards,
  queue table, top-paths section, embedded scorecards rollup, domain
  score matrix, recommended next steps, coverage detail, method,
  orientation.
- Spot-check by reading the rendered HTML cold — the report should
  answer "where bots are costing money?" at a director-of-engineering
  reading level: headline names the cost concentration (or the lead
  triggered rule when cost is missing), top-paths table names the
  expensive paths, evidence cards explain why each entity ranked.
