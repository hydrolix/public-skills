# SOC Triage — port from legacy renderer

## Context

`executive_posture` is now in `report_engine` as the **Bot & Edge Movement
brief** with a deterministic actionable-summary Finding, per-metric
verdict strip, traffic-weighted lead, and dual-route through
`render_report.py`. We just established three working contexts in the
engine: `scorecard_brief`, `scorecard_entity_review`, `executive_posture`
(plus a `control_review` stub).

Next walk-through: `soc_triage`. Two reasons to take it next:

1. **Highest reuse leverage of any remaining report.** Its data model is
   the same `bot_scorecard_artifacts.v1` packet that `scorecard_brief`
   already handles — `bot_scorecard_index.v1` ranking + a list of
   `bot_entity_scorecard.v1` cards. The work is mostly relabeling /
   relensing rather than authoring new primitives, so it's a clean test
   of whether the seven resolutions transfer to a different audience
   (SOC analyst rather than platform/edge-ops director).
2. **It currently isn't in `report_engine` at all.** No stub, no
   template, no context preparer. HTML wrappers for `soc_triage` fall
   through to `render_html` → `markdown_to_simple_html(md_soc(...))`,
   which ships the SOC analyst legacy markdown wrapped in a thin HTML
   shell.

Outcome: an HTML brief that reads to a SOC analyst — "SOC Triage — top
risky entities" — and an end-to-end render path through `report_engine`
that mirrors the scorecard brief's structure with a security lens.

## Title (locked)

The legacy default was "Bot Insights SOC Triage." That's fine but
generic. Lock to:

- **H1**: `SOC Triage — {{cluster}}, {{entity_type_label}} risk queue`
  - `entity_type_label` is humanized: `client_asn` → "ASN",
    `request_host` → "host", `client_ip` → "IP". Fall back to
    `humanize_identifier(entity_type)` for unknown types.
- **Kicker**: `Bot Insights — security risk triage`
- **Dek**: `Top entities ranked by mechanical risk indicators for the
  current window.`

The machine identifier (`report_type: "soc_triage"`) stays — only the
displayed title changes. No producer breakage.

## What the report shows (audit of the example artifact)

`examples/soc-triage.json` bundles a single artifact:

- `bot_scorecard_artifacts.v1` — packet wrapping a
  `bot_scorecard_index.v1` (2 ranked client_asn entities, scope
  `request_host=www.example.com`) and 2 `bot_entity_scorecard.v1` cards.
- Top entity (ASN 64500, score 58, medium_review, primary domain
  `security_evidence`): triggered `bad_bot_share_high` (65%),
  `siem_auth_fail_present` (200 fails), `siem_blocked_present` (1200
  blocked), `volume_delta_high` (+340K req, +425%),
  `bot_share_delta_high` (+44.1pp). 11 features missing inputs.
- Second entity (ASN 64600, score 12, observe, primary domain
  `security_evidence`): only `siem_blocked_present` (40 blocked)
  triggered. 13 features missing inputs.
- Wrapper carries one `llm-interpretation` analyst note.

Producer schema variation to handle: the SOC fixture uses
`features` + `not_evaluated_features`; the scorecard_brief fixture uses
both those AND a derived `rule_results` field. The engine needs an
adapter that normalizes either shape. The legacy code has one already
(`render_report.py::scorecard_rule_results`).

The report's job: rank entities by risk, foreground the security
evidence (SIEM, bad-bot share), and flag the coverage gaps so the
analyst doesn't read "score 12" as "this entity is safe."

## Resolutions (mirror the seven from the brief)

### 1. Per-entity verdict pill (analog of the per-host triage pill)

Reuse `verdicts.classify()` directly — the 4-state taxonomy
(Assign / Watch / Insufficient data / Close — expected) carries over
unchanged. Same band-tier mapping (`escalate`, `monitor`, `observe`),
same coverage-gap rule. The per-entity row renders the verdict pill;
the fleet aggregates into the shared `triage_strip` macro.

For the example: 64500 → Assign (medium_review band, 5 rules
triggered), 64600 → Insufficient data (observe band, 1 rule triggered
but 13 of 14 inputs missing → ratio above the 0.5 threshold AND the
verdict logic kicks insufficient_data only when no rule triggered).
Wait — 64600 *did* trigger `siem_blocked_present`, so by the rule
documented in `verdicts.py` it stays in its trigger-derived state
(Watch) and surfaces the coverage gap via the confidence chip rather
than getting demoted. Verify against the classifier.

### 2. Director-grade recommendation (synthesized in the context preparer)

The producer schema (`bot_entity_scorecard.v1`) emits
`recommended_next_steps` per scorecard already. Two grades:

- The `_aggregate_actions` helper in `scorecard_brief.py` already
  collapses these across the fleet by detail text. **Reuse it.** Same
  per-entity dict shape feeds both the executive summary (uses the
  short `summary` form) and the inlined actions section (uses the
  analyst-grade `detail` form).
- For the executive summary lead, synthesize a security-lensed
  `actionable_summary` Finding the same way the brief does. The
  headline rules:
  - Top entity is in Assign and primary domain is `security_evidence`:
    "N of M entities need analyst attention — start with `<entity>`
    (SIEM evidence: <signal>)" where `<signal>` is the most-impactful
    triggered security_evidence feature.
  - Top entity is in Assign but primary domain is something else (e.g.
    `movement`): "N of M entities need analyst attention — start with
    `<entity>` (volume +X, bot share +Ypp)."
  - Only Watch entities: "N of M entities to watch."
  - All Insufficient: "N of M entities cannot be judged from this
    report alone."
  - All Close: "All N entities read clean."

### 3. Traffic-weighted framing in the lead

When `entity_metrics.current_requests` is present on every scorecard
(no missing volume), compute the top entity's share of fleet requests
and append the share clause:

> "ASN 64500 needs analyst attention (covers 84% of fleet requests this
> window) — bad-bot share 65%."

The example artifact does NOT carry `entity_metrics` per scorecard, so
for the example the share clause is omitted — same "don't fabricate"
rule as the brief. The fixture for traffic-weighted framing is one we
add explicitly.

### 4. Insufficient_data demotion (same rule, documented)

No new code. Use `verdicts.classify(band, rule_counts)` exactly as
`scorecard_brief.py` does. The doc comment in `verdicts.py` already
covers SOC's case (entity that triggered something keeps its verdict;
coverage gap surfaces via chip).

### 5. Zero-count pill muting

Reuse the `pill-muted` class on the per-entity verdict strip. No code
change beyond reusing the existing macro.

### 6. Caveat copy

Use "Real risk may be higher than the score implies." (the same copy
as the scorecard brief — the SOC framing actually fits this phrasing
better than the brief's, since "risk" is the SOC reader's word).

### 7. Single source of truth for action selection

`_aggregate_actions` returns `{summary, detail, host_count, preview,
extra}` per action. Both the executive-summary lead and the inlined
actions section consume the same dict. Same single-derivation rule the
brief and the movement brief both follow.

## Macros — reused vs. new

Reused (no change):

- `templates/macros/executive_summary.html` — same structure (bold lead,
  italic body on its own line, recommendation callout, caveat callout).
- `templates/macros/triage_strip.html` — same markup; new context
  populates `pills` with entity-verdict counts.
- `templates/macros/queue_table.html` — same data table; sorts entities
  by verdict state then score.
- `templates/macros/coverage_table.html`, `templates/macros/method.html`,
  `templates/macros/report_purpose.html`.

New / adapted:

- `templates/reports/soc_triage.html` — top-level layout.
- `templates/macros/security_evidence_cards.html` — per-entity card,
  foregrounds the `security_evidence` domain (SIEM features, bad-bot
  share, auth-fail / blocked-request counts) and lists adjacent
  triggered features below. One card per Assign or Watch entity.
- `templates/macros/domain_score_matrix.html` — entities-rows ×
  domains-columns grid showing per-cell point totals. Empty cells
  render as muted dashes; cells with points render as a tinted pill
  (escalate-tinted at high points, monitor-tinted at lower).

Inlined directly in `soc_triage.html` (do not reuse `actions.html`):

- Per-entity actions section. Same pattern we used in
  `executive_posture.html` — `actions.html` hard-codes the word "host",
  which is wrong for SOC where the unit is "entity" (often an ASN).
  Inline a metric/entity-aware version.

## Critical files

| File | Purpose |
|------|---------|
| `skills/bot-insights/scripts/report_engine/scorecards.py` (NEW) | Promote `render_report.py::scorecard_rule_results` to a shared adapter that normalizes a scorecard's `rule_results` field, falling back to a synthesized list from `features` + `not_evaluated_features` when the producer didn't emit `rule_results`. Both `scorecard_brief.py` and the new `soc_triage.py` call it; the SOC fixture's older shape works without producer changes. Also expose `rule_counts(card)` to centralize the `{triggered, below_threshold, missing_input, total}` projection currently duplicated in `scorecard_brief.py` and `scorecard_entity_review.py`. |
| `skills/bot-insights/scripts/report_engine/contexts/soc_triage.py` (NEW) | `assemble()` reshapes a wrapper's artifacts into `{index, scorecards}` (handling both the bundled `bot_scorecard_artifacts.v1` packet shape and the flat-list shape — same fallback as `scorecard_brief.assemble()`). `prepare()` projects entities into per-entity verdicts, builds the triage strip, synthesizes the security-lensed `actionable_summary` Finding, and emits queue rows + security evidence cards + domain score matrix. Reuse `_aggregate_actions`, `_lowest_host_callout` (renamed semantically as `_top_entity_callout`), `_entity_row` from `scorecard_brief.py` — promote what's shared to a tiny helper module if the diff stays small, otherwise import directly. |
| `skills/bot-insights/scripts/report_engine/contexts/__init__.py` (MOD) | Register `soc_triage` in `_MODULES`. Note: `SCHEMA_REGISTRY` keys on `schema_version` and `bot_scorecard_artifacts.v1` is already mapped to `scorecard_brief` for raw-artifact mode. Keep that mapping; SOC routing flows through `REPORT_TYPE_REGISTRY` via the wrapper's `report_type` (the same path `executive_posture` uses). Document this constraint in the module docstring. |
| `skills/bot-insights/scripts/report_engine/templates/reports/soc_triage.html` (NEW) | Hero (executive summary), then verdict strip, then security evidence cards (top Assign/Watch entities), then queue table (full ranking), then domain score matrix, then coverage detail (disclosure), then orientation disclosure + method. |
| `skills/bot-insights/scripts/report_engine/templates/macros/security_evidence_cards.html` (NEW) | Per-entity card macro. |
| `skills/bot-insights/scripts/report_engine/templates/macros/domain_score_matrix.html` (NEW) | Entity-by-domain grid macro. |
| `skills/bot-insights/scripts/report_engine/templates/_styles.css` (MOD) | Card / matrix styles. Reuse pill / chip / queue / verdict-strip styles already in place. |
| `skills/bot-insights/scripts/report_engine/contexts/scorecard_brief.py` (MOD) | Switch the `_rule_counts(sc)` callsite over to `scorecards.rule_counts(card)`. Adopt `scorecards.normalize_rule_results(card)` so a SOC-style scorecard (no `rule_results` field) can flow through the same code path. Behavior on the existing navy federal fixture is unchanged. |
| `skills/bot-insights/scripts/report_engine/contexts/scorecard_entity_review.py` (MOD) | Same — adopt the shared adapter for the rule_results / rule_counts derivation. |
| `skills/bot-insights/scripts/render_report.py` (MOD) | Extend `_render_executive_posture_via_engine` into a generic `_render_via_engine(report_type=...)` and route HTML for `soc_triage` wrappers through it. Markdown stays on `md_soc`. |
| `skills/bot-insights/scripts/bot_insights_report.py` (MOD) | Update wrapper title default for soc_triage from the auto-Title-Case "Bot Insights Soc Triage" to "SOC Triage" (the auto-generated form lowercases the acronym, which reads wrong). |
| `tests/fixtures/report_engine/soc_triage_*.json` (NEW) | Three fixtures: a full wrapper (mirror `examples/soc-triage.json`); a degraded fixture (index only, no scorecards — exercises the `ranking-only` fallback path the legacy renderer already supports via the `compatible_scorecards_for_index` warning); a single-entity fixture (N=1 — verify the report doesn't fall apart at fleet=1 and frames the headline correctly). |
| `tests/snapshots/report_engine/soc_triage_*.html` (NEW) | Snapshot baselines. |
| `tests/test_report_engine.py` (MOD) | Add render tests against the three fixtures. Spot-check assertions for: title, top-entity recommendation, traffic-weight clause when applicable, mute-zero-count pills, security-evidence cards, "Real risk may be higher" caveat. |

## Reused helpers (do not re-implement)

- `report_engine.verdicts` — `STATE_ORDER`, `STATE_LABELS`, `STATE_TONE`,
  `classify`, `confidence_chip`. Same 4-state taxonomy.
- `report_engine.findings.Finding` — same dataclass.
- `report_engine.formatters.format_share_pct`, `signed_pct`, `signed_pp`,
  `big_number`, `window_fmt`.
- `report_engine.humanize.humanize_identifier`, `humanize_band`,
  `humanize_confidence`, `cluster_display`. Add `humanize_entity_type`
  for ASN/host/IP labels (small new helper in `humanize.py`).
- `report_engine.theme.DOMAIN_LABELS`, `DOMAIN_ORDER`.
- `report_engine.charts.triage_histogram_svg` — reuse for the per-entity
  state distribution.
- `scorecard_brief._aggregate_actions`, `_entity_row` — import directly
  if the call signature fits, otherwise lift to a `_shared.py` helper
  module. Don't fork them.
- Legacy `render_report.py::scorecard_rule_results` — port to
  `report_engine/scorecards.py` as documented above.

## Verification

End-to-end render against the bundled example:

```
uv run --with jinja2 --with markdown-it-py --with bleach \
  python skills/bot-insights/scripts/report_engine/render.py \
    --artifact skills/bot-insights/examples/soc-triage.json \
    --out /tmp/soc-triage.html \
    --input wrapper
open /tmp/soc-triage.html
```

Expected outcomes for the example artifact:

- Title reads "SOC Triage — www.example.com, ASN risk queue".
- Executive summary leads with: "1 of 2 entities needs analyst
  attention — start with ASN 64500 (bad-bot share 65%, SIEM evidence
  present)" or close to that. Italicized body line under the bold lead
  gives the queue-state clarification.
- Recommendation callout reads the producer-style short form drawn
  from the entity's `recommended_next_steps`.
- Caveat callout fires: "Real risk may be higher than the score
  implies." (Coverage is thin — both entities have ≥ 50% missing
  inputs.)
- Per-entity verdict strip with mute-zero-counts: 1 Assign, 0 Watch
  (muted), 1 Insufficient data, 0 Close (muted). (Or: 1 Assign, 1
  Watch with a Low-confidence chip — exact split depends on
  classifier output for 64600; verify.)
- Security evidence cards render for the Assign entity (64500),
  surfacing `bad_bot_share_high`, `siem_auth_fail_present`,
  `siem_blocked_present`, plus the volume/share movement features.
- Queue table lists both entities, sorted Assign-first by verdict
  state then by score (lowest first → most risk first since the SOC
  scoring convention is high score = high risk).
- Domain score matrix shows `movement` and `security_evidence` as the
  active domains for 64500; only `security_evidence` for 64600.

Spot-check the degraded fixture (index-only, no scorecards): ranking
table renders, security evidence cards section is empty (or shows a
"degraded mode" inline note), queue table falls back to producer-rank
data only.

Spot-check the N=1 fixture: triage strip reads "1 entity needs analyst
attention" or analogous singular form; queue table is one row.

Tests:

```
uv run pytest tests/test_report_engine.py tests/test_skill_scripts.py
uv run ruff format <edited paths>
uv run ruff check --fix <edited paths>
uv run mypy skills/bot-insights/scripts/report_engine/
```

Snapshots are net-new for `soc_triage_*` and may shift for the
`scorecard_brief_*` baselines (CSS additions ripple). Regenerate with
`REPORT_ENGINE_UPDATE_SNAPSHOTS=1` once and diff each against expected
outcomes before committing.

Pre-existing mypy errors (jinja2 / markupsafe / bleach stubs,
`scorecard.py` line 96 dict typing) are unchanged by this work.

## Out of scope

- Renaming the `report_type` machine identifier from `soc_triage`
  → anything else. Producer breakage. Park.
- Removing `md_soc` from `render_report.py`. Dual-route stays — same
  agreement as for `executive_posture`. Cleanup deferred until all
  remaining report types (`control_review`, `crawler_governance`,
  `edge_ops_impact`) have engine contexts.
- Singleton-promotion of an N=1 SOC wrapper to a `soc_entity_review`
  context. Defer until needed; render N=1 through the fleet view at
  size 1 (the queue table reads fine at that size).
- Surfacing `bot_timeseries.v1` sparklines if the wrapper bundles
  them. Same feature-flag posture as the movement brief; ship without
  and add when needed.
- Apply the same patterns to `control_review`, `crawler_governance`,
  `edge_ops_impact`. Tracked as the next walk-through after this one
  lands. After SOC, the natural sequencing is `control_review` (lens-
  distinct, before/after framing) → `crawler_governance` +
  `edge_ops_impact` together (they share the legacy `md_domain_report`
  shape — should share an engine base context module).
- Extracting a written design-pattern doc / updating the bot-insights
  `SKILL.md` guidance. Tracked for after we've built 2–3 more report
  types and have enough variation to call out universal vs situational
  patterns.
