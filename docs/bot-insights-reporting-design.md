# Bot Insights Reporting and Visualization Design

Status: accepted design specification  
Date: 2026-04-19

## Summary

Specify thin, demo-friendly report generation support for the `bot-insights`
skill. This document is an accepted design specification. This worktree already
contains an initial `skills/bot-insights/scripts/render_report.py`; the
requirements and phases below distinguish existing implementation from remaining
design requirements.

The report layer should present existing Bot Insights artifacts as Markdown or
self-contained HTML with lightweight SVG visualizations. It should
not query Hydrolix, generate SQL, open database connections, or become a
general dashboard framework.

The core design principle is:

> Artifacts are the source of truth. Reports are deterministic views over those
> artifacts. LLM-authored analyst notes may summarize and explain, but they must
> be clearly labeled as interpretation, cite specific artifact fields where
> possible, and must not invent, score, rank, or infer beyond the artifact
> constraints.

## Goals

- Produce polished demo reports from Bot Insights outputs with little setup.
- Keep report generation deterministic and reproducible from saved JSON.
- Support high-signal visualizations for executive, SOC, SEO/crawler
  governance, Edge/Ops impact, and control-review demos.
- Preserve artifact metadata, confidence labels, confidence reasons, time
  windows, scopes, table names, and interpretation constraints.
- Keep the implementation dependency-light and portable across skill hosts.
- Make missing evidence visible instead of silently treating it as safe or zero.

## Non-Goals

- Do not build a dashboard product.
- Do not add Hydrolix clients, credentials, connection settings, or direct query
  execution to local scripts.
- Do not create a browser-side application framework.
- Do not use external CDN assets or JavaScript charting libraries for the MVP.
- Do not recompute bot metrics, risk scores, confidence, rates, or rankings in
  the report renderer.
- Do not infer causality from movement artifacts.
- Do not force all reports into one rigid template.

## Existing Boundary

The current `bot-insights` skill already has a useful separation of concerns:

- Hydrolix filters, groups, and aggregates large data sets.
- Local scripts accept MCP query results, saved aggregate JSON, or pasted JSON.
- `compare_posture.py` emits versioned posture, mover, and control-review
  artifacts.
- `scorecard.py` emits deterministic entity scorecards and ranked indexes.
- Existing artifact constraints tell downstream summarizers to use structured
  evidence only and avoid unsupported causal claims.

The report layer should sit after those scripts:

```text
Hydrolix query or MCP result
  -> aggregate JSON
  -> compare_posture.py / scorecard.py
  -> versioned artifacts
  -> render_report.py
  -> Markdown or self-contained HTML report
```

## Proposed Components

### 1. Reporting Reference

Maintain this accepted reporting reference as the source of truth for:

- supported report types;
- required and optional artifact schemas per report type;
- section ordering;
- visualization mapping by artifact schema;
- demo workflow examples;
- language and interpretation guardrails.

Linking this document from `SKILL.md` is a phase-dependent rollout decision. It
may remain deferred until the renderer behavior is stable enough for normal
skill users, but that deferral does not change this document's status as the
accepted implementation reference.

### 2. Report Renderer Script

The local renderer entrypoint is:

```text
skills/bot-insights/scripts/render_report.py
```

The worktree contains an initial implementation. This design remains the source
of truth for required validation, degradation, and rendering behavior.

The script should:

- read JSON from `--file`, positional text, or stdin;
- accept existing artifact JSON or a wrapper `bot_report_input.v1` object;
- emit Markdown or self-contained HTML;
- render inline SVG charts for HTML output;
- emit plain tables for Markdown output;
- avoid non-stdlib dependencies for the MVP;
- reject unknown schemas by default.

Example CLI:

```bash
python skills/bot-insights/scripts/render_report.py \
  --file artifacts.json \
  --report-type executive_posture \
  --format html \
  --output bot-report.html
```

Optional CLI flags:

- `--file <path>`
- `--format markdown|html` (default: `markdown`)
- `--report-type executive_posture|soc_triage|control_review|scorecard_brief|crawler_governance|edge_ops_impact`
- `--output <path>`
- `--limit <n>` where `<n>` must be a positive integer display limit
- `--allow-unknown`
- `--title <text>`

### 3. Accepted Report Input Grammar

The renderer should accept exactly these top-level JSON shapes for the MVP:

- A single known artifact object, identified by a supported artifact
  `schema_version` other than `bot_report_input.v1`.
- A non-empty top-level array of known artifact objects.
- A wrapper object with `schema_version: "bot_report_input.v1"`.

Raw single-artifact input is a convenience form. It is normalized as if the
caller supplied a wrapper with one artifact, no analyst notes, no wrapper title,
no wrapper scope label, and no wrapper row limit. `--report-type` should be
provided for raw single-artifact input. If it is omitted, the renderer may infer
the report type only when the single artifact schema maps to exactly one valid
report type under the validation rules below; otherwise it must fail with an
ambiguous or missing report intent error.

MVP raw single-artifact inference is intentionally narrow:

- `bot_posture_movement.v1` infers `executive_posture`.
- `bot_control_review.v1` infers `control_review`.
- `bot_scorecard_index.v1` infers `soc_triage`.
- `bot_mover_attribution.v1` does not infer a report type.
- `bot_entity_scorecard.v1` does not infer a report type because it can satisfy
  `scorecard_brief`, `crawler_governance`, or `edge_ops_impact`.
- `bot_scorecard_artifacts.v1` does not infer a report type because it can
  satisfy multiple scorecard-dependent report types after normalization.

When inference is unavailable, raw single-artifact input must fail with a clear
message asking the caller to provide `--report-type`.

Raw array input is also a convenience form. It is normalized as if the caller
supplied a wrapper whose `artifacts` field is that array in the original order,
with no analyst notes, no wrapper title, no wrapper scope label, and no wrapper
row limit. Because a raw array has no durable report intent or citation context,
`--report-type` is required for raw arrays. Multi-artifact reports may use raw
arrays for one-off CLI demos, but wrappers are preferred for reusable demos,
stable artifact IDs, scope labels, and analyst-note citations.

Unknown artifacts are rejected in every input shape unless `--allow-unknown` is
set. `--allow-unknown` permits unknown artifacts to be listed as skipped input;
it does not make them eligible to satisfy required-artifact validation.

### 4. Report Intent and Option Resolution

The renderer should resolve report intent and presentation options before
required-artifact validation:

- `resolved_report_type` comes from the wrapper `report_type`, CLI
  `--report-type`, or the limited raw single-artifact inference described above.
- If both wrapper `report_type` and CLI `--report-type` are supplied, they must
  match exactly. Conflicting report-type values are a hard failure.
- If neither wrapper nor CLI supplies a report type, and raw single-artifact
  inference is unavailable or ambiguous, the renderer must fail closed instead
  of trying multiple report templates.
- `resolved_title` comes from CLI `--title` when provided; otherwise it comes
  from wrapper `title`; otherwise the renderer generates a generic title from
  the resolved report type and available scope metadata. CLI `--title` is an
  intentional presentation override, not a hard conflict.
- `resolved_format` comes from CLI `--format` when provided; otherwise it
  defaults to `markdown`. HTML is the primary demo format for the full MVP, but
  the CLI default remains Markdown for portable stdout use.
- `resolved_limit` comes from CLI `--limit` when provided; otherwise it comes
  from wrapper `limit` when provided; otherwise the report type's default
  display limits apply. CLI `--limit` must be a positive integer display limit;
  zero, negative, or non-integer values are hard failures. The resolved display
  limit limits rendered rows or cards only; it does not select source artifacts,
  trim the normalized collection, or affect required-artifact validation. CLI
  `--limit` is an intentional display override, not a hard conflict.
- If CLI `--title` or `--limit` overrides a wrapper value, the renderer should
  emit a diagnostic warning to stderr and the report warning section so the
  rendered artifact remains reproducible from its invocation.
- Wrapper `scope_label` has no CLI override in the MVP. When present, render it
  as presentation scope text. It is not compatibility evidence and must not
  override artifact `scope` metadata used for validation or artifact pairing.
- When wrapper `scope_label` is absent, the renderer may display artifact
  `scope` metadata only when it is present and unambiguous across the selected
  report artifacts. If artifact scope metadata is absent, unknown, or mixed,
  render scope as unavailable or mixed with a visible warning or evidence-limit
  entry as appropriate.
- Required-artifact validation must use `resolved_report_type` after input
  normalization and scorecard-packet decomposition, and before applying row
  display limits. Display limits cannot hide required artifacts or make a
  required artifact appear absent.

Default display limits:

- executive posture: 10 rows or cards per limited section;
- SOC triage: 10 rows or cards per limited section;
- control review: 10 rows or cards per limited section;
- scorecard brief: 20 rows per limited feature or evidence section;
- crawler governance: 10 rows or cards per limited section;
- Edge/Ops impact: 10 rows or cards per limited section.

These defaults apply only when neither CLI `--limit` nor wrapper `limit` is
provided. A resolved display limit applies uniformly to limited report sections
unless a future explicit section config defines per-section limits.

### 5. Optional Report Input Wrapper

The renderer should accept raw known artifacts for convenience, but the most
predictable path is a wrapper object:

```json
{
  "schema_version": "bot_report_input.v1",
  "report_type": "executive_posture",
  "title": "Bot Posture Report",
  "scope_label": "www.example.com",
  "limit": 10,
  "artifacts": [
    {
      "artifact_id": "posture-week-1",
      "schema_version": "bot_posture_movement.v1",
      "comparison_type": "week_over_week",
      "metrics": []
    },
    {
      "artifact_id": "scorecard-index-1",
      "schema_version": "bot_scorecard_index.v1",
      "ranked_entities": []
    }
  ],
  "analyst_notes": [
    {
      "note_id": "note-1",
      "author_type": "llm",
      "text": "Bot share increased in the current window, with sparse-count caveats on one supporting metric.",
      "data_sources": [
        {
          "artifact_id": "posture-week-1",
          "schema_version": "bot_posture_movement.v1",
          "json_pointer": "/metrics/0/absolute_delta",
          "label": "bot_share_pct absolute delta"
        },
        {
          "artifact_id": "posture-week-1",
          "schema_version": "bot_posture_movement.v1",
          "json_pointer": "/metrics/0/confidence_reasons",
          "label": "metric confidence reasons"
        }
      ]
    }
  ]
}
```

The wrapper allows demo authors to specify report intent without asking the
renderer to infer everything from artifact shape. Wrapper `artifacts` contain
raw known artifact objects, not a second nested `data` envelope.

Wrapper validation rules:

- `schema_version` must be `bot_report_input.v1`.
- `report_type`, when present, must be one of the supported report types. For
  reusable fixtures, wrappers should include `report_type` rather than relying
  on CLI flags.
- `limit`, when present, must be a positive integer display limit. It limits
  rendered rows or cards only; it does not select source artifacts, trim the
  normalized collection, or affect required-artifact validation.
- `artifacts` must be a non-empty array of known artifact objects unless
  `--allow-unknown` is set.
- Each artifact may include `artifact_id`. If provided, it must be unique in the
  wrapper.
- If `artifact_id` is omitted, the renderer assigns a stable internal ID from
  input order, such as `artifact-1`. Generated IDs are valid only for the
  current render and should not be used in reusable analyst-note fixtures.
- Duplicate wrapper `artifact_id` values are hard failures.
- Explicit artifact IDs must not use reserved generated child suffixes,
  including `#index` and `#scorecard-N`, because those suffixes are reserved
  for children produced from `bot_scorecard_artifacts.v1`.
- Multiple artifacts with the same `schema_version` are allowed only when the
  report type can consume all of them unambiguously, such as multiple
  `bot_entity_scorecard.v1` artifacts in SOC triage or scorecard matrices.
- If a report type requires a single primary artifact and multiple candidates
  are present, the renderer should fail with an ambiguity error rather than
  silently choosing one.
- Exact duplicate artifact bodies without explicit IDs should be deduplicated
  before report selection, with a visible warning that duplicate input was
  ignored. If duplicate artifacts have explicit IDs, are referenced by analyst
  notes, or deduplication could affect report selection, ranking, or citations,
  treat the duplicate as a hard failure instead of silently choosing one.
- Artifact IDs used for citations must refer to the normalized artifact
  collection described below, not just the original wrapper array.

`analyst_notes` are optional and are treated as untrusted interpretation. They
are in scope for demos, but they must never become a second data model.

Analyst-note structural validation is fail-closed. A wrapper with malformed
`analyst_notes` must fail before rendering any report rather than skipping notes
silently or rendering partial note content.

Required note fields:

- `author_type`: `llm` or `analyst`;
- `text`: non-empty plain-text string.

Optional note fields:

- `note_id`: stable identifier for diagnostics and tests;
- `title`: short label for the note;
- `created_at`: timestamp string supplied by the caller;
- `data_sources`: cited artifact fields.

Required data-source fields:

- `json_pointer`: RFC 6901 pointer string into the cited normalized artifact;
- at least one selector: `artifact_id` or `schema_version`.

Data-source selector fields:

- `artifact_id`: preferred artifact identifier and primary selector;
- `schema_version`: optional consistency check when `artifact_id` is supplied,
  or fallback selector when no `artifact_id` is supplied and exactly one
  normalized artifact has that schema.

Optional display fields:

- `label`: display label for the citation.

Analyst-note validation rules:

- Missing `text`, non-string `text`, or empty `text` is a hard failure.
- Missing `author_type`, non-string `author_type`, or an unsupported
  `author_type` value is a hard failure.
- `data_sources`, when present, must be an array. A non-array `data_sources`
  value is a hard failure.
- Every data-source entry must be an object. Non-object entries are hard
  failures.
- Each data-source entry must include a string `json_pointer`. Missing or
  non-string `json_pointer` is a hard failure.
- `json_pointer` must be syntactically valid RFC 6901. Malformed pointer syntax
  is a hard failure. The empty pointer `""` is valid and cites the whole
  normalized artifact.
- `artifact_id`, `schema_version`, and `label`, when present, must be strings.
  Non-string selector or label values are hard failures.
- A structurally valid but unresolved citation is a hard failure. Analyst notes
  are untrusted interpretation, but if supplied citations cannot be resolved,
  the rendered report would make citation integrity ambiguous.
- A structurally valid note with no `data_sources` is allowed but must produce a
  visible warning.

Analyst-note rendering rules:

- Render notes in a distinct Analyst Notes section.
- Label `author_type: llm` as LLM interpretation and `author_type: analyst` as
  analyst interpretation.
- State once in the section that notes are interpretive narrative, not facts
  strictly proven by the artifact data alone.
- Escape note text, titles, labels, and resolved values for Markdown and HTML.
- Show each cited data source when provided.
- Warn when a note has no cited data sources.
- Fail when a data source cannot be resolved. Citation failures include missing
  artifacts, `schema_version` mismatches, ambiguous schema-only selectors,
  malformed JSON Pointer syntax, or JSON Pointers that do not resolve within the
  selected normalized artifact.
- Resolve references by `artifact_id` first. If `schema_version` is also
  supplied, verify that the resolved artifact has that schema; a mismatch is a
  citation failure. If only `schema_version` is supplied, resolve it only when
  exactly one normalized artifact has that schema; otherwise fail because the
  schema-only citation is ambiguous.
- Allow notes to cite normalized child artifacts created from
  `bot_scorecard_artifacts.v1`. For example, a parent ID
  `scorecard-pack-1` produces child IDs `scorecard-pack-1#index` and
  `scorecard-pack-1#scorecard-1`.
- Never use analyst notes as input for metric values, chart values, scores,
  ranks, confidence, report selection, duplicate detection, or row-limit
  calculations.

### 6. Artifact Normalization

Before report validation, the renderer should normalize artifact inputs into a
working collection:

- retain every raw artifact for metadata, citation, duplicate detection, and
  evidence-limit rendering;
- assign each raw artifact a stable render-time `artifact_id`;
- automatically decompose `bot_scorecard_artifacts.v1` into its nested
  `index` and `scorecards`;
- treat the nested `index` as a usable `bot_scorecard_index.v1` artifact with a
  child ID such as `<parent_id>#index` only when the nested value is an object
  whose `schema_version` is `bot_scorecard_index.v1`;
- treat each nested scorecard as a usable `bot_entity_scorecard.v1` artifact
  with child IDs such as `<parent_id>#scorecard-1` only when the nested value is
  an object whose `schema_version` is `bot_entity_scorecard.v1`;
- preserve `parent_artifact_id` and the parent JSON Pointer for each child so
  rendered sections and analyst-note citations can explain where the child came
  from;
- require artifact IDs to be unique across the full normalized collection,
  including raw artifacts and generated scorecard-packet children;
- treat normalized ID collisions as hard failures because they make
  analyst-note citations ambiguous;
- reject or warn on duplicate artifacts according to report-type needs, rather
  than silently choosing one;
- preserve artifact order after normalization, with parent artifacts before
  their derived children and child scorecards in packet order.

This normalization lets report types depend on the data they need without
forcing callers to manually split scorecard artifact packets.

`bot_scorecard_artifacts.v1` never satisfies a report requirement by its parent
schema alone. A packet is usable for scorecard-dependent report sections only
when normalization produces at least one valid nested
`bot_entity_scorecard.v1`. SOC scorecard-dependent sections additionally require
a valid nested or standalone `bot_scorecard_index.v1` plus compatible scorecards
under the rules below. Empty packets, packets with missing `scorecards`, packets
whose nested scorecards are not objects, and packets with malformed nested
schema versions remain visible as raw input metadata but do not satisfy
required-artifact validation. If the selected report or selected section needs
one of those packets to produce required child artifacts, validation fails
closed. If another valid artifact set satisfies the selected report, extra empty
or malformed packets are warnings or evidence-limit inputs only and must not
block rendering.

### 7. Cross-Artifact Compatibility

After normalization and before required-artifact validation, the renderer must
prove that artifacts used together are compatible. "Related" means compatible
under this section, not merely adjacent in input order or sharing a schema name.
The renderer must never silently pair a scorecard index from one host, table, or
window with entity scorecards from another.

Compatibility fields are compared from artifact metadata without inference:

- `scope`: canonical JSON equality after sorting object keys. Wrapper
  `scope_label` is presentation text only; it does not prove compatibility and
  does not override artifact `scope` metadata.
- `current_window`: exact equality for schemas that describe
  current-versus-baseline state.
- `baseline_windows`: exact equality, including the set of baseline windows and
  their labels when present.
- `comparison_type`: exact string equality when present on both artifacts.
- `table_used`: exact string equality when present on both artifacts.
- `entity_type` and entity key: exact equality for scorecard-index to
  scorecard pairing. The scorecard entity key is `entity_type` plus `entity`.
  The index row key must come from the corresponding ranked-entity fields; if
  those fields are missing, the row cannot be paired.

For compatibility checks, absent fields and empty placeholder values are
unknown, not known-compatible values. Unknown values include absent fields,
`null`, `{}`, `[]`, and `""`. Two unknown values do not prove compatibility.
Unknown metadata may still be rendered as unavailable, but it must not be used to
join artifacts into a shared narrative or chart.

Scorecard index-to-scorecard pairing uses this MVP proof:

- `entity_type` and entity must match exactly.
- Children decomposed from the same `bot_scorecard_artifacts.v1` parent are
  packet-compatible after `entity_type` and entity match. If shared metadata is
  present with known values on both child artifacts, those values must match.
  Unknown shared metadata on same-packet children is a degraded warning, not a
  hard failure; render the missing fields as unavailable and list the metadata
  gap in evidence limits. This exception applies only to children from the same
  parent packet because the packet producer emitted the index and scorecards as
  one artifact set.
- Standalone index-to-scorecard pairing, or pairing children from different
  scorecard packets, requires `scope`, `current_window`, `baseline_windows`, and
  `table_used` to be present with non-empty known values on both the index and
  the scorecard, and to match exactly.
- For standalone or cross-packet pairing, `comparison_type`, when present as a
  non-empty known value on either side, must be present as a non-empty known
  value on both sides and must match exactly. For same-packet children, unknown
  `comparison_type` follows the same degraded-warning rule as other unknown
  shared metadata.
- If any required proof field for the relevant pairing mode is unknown or
  mismatched, the scorecard is not compatible with that index row.

Hard failures:

- Required artifacts for a selected report have conflicting `scope`,
  `current_window`, `baseline_windows`, `comparison_type`, or `table_used`
  values.
- A scorecard-dependent report has both a `bot_scorecard_index.v1` and
  scorecards available, but no scorecard can be matched to the index by
  `entity_type` and entity key.
- A scorecard index and matched scorecard disagree on any known compatibility
  field listed above. This includes standalone artifacts and children decomposed
  from `bot_scorecard_artifacts.v1`.
- A required scorecard, index row, posture, mover, or control artifact is
  missing the entity fields needed to prove the specific required-artifact
  relationship.
- A standalone or cross-packet required-artifact relationship is missing the
  metadata fields needed to prove compatibility. This hard failure does not
  apply to unknown shared metadata on children from the same
  `bot_scorecard_artifacts.v1` packet, which is a degraded warning after
  `entity_type` and entity match.

Degraded warnings:

- Optional artifacts such as posture, mover, or control artifacts are missing
  compatibility metadata. The report may still render the primary artifact set,
  but it must not combine the optional artifact into a shared narrative or chart
  that implies the same scope/window/table.
- Optional artifacts have conflicting compatibility fields. The renderer should
  keep them out of combined sections, list them in evidence limits, and warn
  visibly instead of treating them as related.
- Extra scorecards are present but do not appear in the selected index. They may
  be listed as unranked context or evidence limits only; they must not be shown
  in index-ranked sections.
- An index contains ranked entities for which no compatible scorecard is
  available. The index ranking may still render in degraded mode, but
  scorecard-dependent sections must omit those entities and warn.
- Same-packet scorecard children are missing shared `scope`, `current_window`,
  `baseline_windows`, or `table_used` metadata. Scorecard-dependent sections may
  render because the packet proves shared origin, but the renderer must mark the
  missing fields as unavailable and list the metadata gap in evidence limits.

Report-specific compatibility rules:

- MVP SOC triage has one implicit mode. If only a valid
  `bot_scorecard_index.v1` is available, it renders a degraded ranking-only
  report. If one or more scorecards are available and compatible with index rows
  under the packet or standalone pairing rules above, scorecard-dependent
  sections may render for the compatible scorecards. If scorecards are present
  but none are compatible with the index, the report fails rather than pretending
  feature evidence belongs to the ranked entities.
- Future explicit section selection may introduce a "full SOC required" mode in
  which requested scorecard-dependent sections fail when compatible scorecards
  are absent.
- Scorecard Brief may use a related index only when the selected scorecard's
  `entity_type` and entity key match exactly one index row and the applicable
  packet or standalone compatibility rule matches.
- Crawler Governance and Edge/Ops preserve compatible index order only for
  scorecards that match the index by entity key and the applicable packet or
  standalone compatibility rule.
  Without a compatible index, they use normalized scorecard input order and
  label it as input order.
- Executive Posture, Control Review, and movement sections may render their
  primary artifact alone. Optional companion artifacts are combined only when
  scope, windows, comparison type, and table metadata are compatible. If
  compatibility metadata is unavailable, render the optional artifact separately
  or list it in evidence limits with a visible degraded warning.

## Supported Artifact Schemas

Initial MVP report support should handle:

- `bot_posture_movement.v1`
- `bot_mover_attribution.v1`
- `bot_control_review.v1`
- `bot_entity_scorecard.v1`
- `bot_scorecard_index.v1`
- `bot_scorecard_artifacts.v1`

Optional future support:

- `bot_timeseries.v1`

MVP rule: `bot_timeseries.v1` is a known future schema, but it is not a
supported MVP schema for required-artifact validation or rendering. MVP
renderers must reject `bot_timeseries.v1` with an unsupported-schema error.
Because it is known-but-unsupported rather than unknown, `--allow-unknown`
does not make it eligible to render or satisfy required artifacts. A future
implementation may add explicit support behind a documented feature flag or a
later schema-support phase.

## Prerequisite Schema Refinement Task

These schema refinements are Phase 0 prerequisites tracked separately from
renderer behavior. They should be completed before reports that depend on the
affected metadata are considered complete.

### Scorecard Rowset and Feature Provenance

Crawler governance needs structured proof that generic rate features came from a
crawler-specific rowset. Existing feature names, free-form supporting metric
labels, and generic `scope` values are not reliable enough to prove that
`rate_429_delta_high` or `rate_5xx_delta_high` represents crawler, good-bot, or
AI-crawler behavior.

Phase 0 should refine scorecard artifacts with explicit structured provenance.
The MVP provenance shape is:

```json
{
  "rowset_scope": {
    "population": "good_bot",
    "filters": {"bot_class": "good_bot"},
    "entity_type": "bot_class",
    "table_used": "bot_summary_day"
  },
  "feature_provenance": {
    "rate_429_delta_high": {
      "rowset_scope": {"population": "good_bot"},
      "metric_inputs": ["current_rate_429_pct", "baseline_rate_429_pct"]
    }
  }
}
```

The required fields and semantics are:

- `rowset_scope.population`, when present, must be one of `crawler`,
  `good_bot`, `ai_crawler`, `all_traffic`, or `unknown`.
- `rowset_scope.filters`, when present, must be a JSON object containing the
  structured filter fields that define the artifact rowset.
- `feature_provenance`, when present, must be a JSON object keyed by scorecard
  feature name. Each feature entry may contain `rowset_scope`, `metric_inputs`,
  and `notes`.
- `feature_provenance.<feature>.rowset_scope.population`, when present, must use
  the same allowed values as artifact-level `rowset_scope.population`.
- `feature_provenance.<feature>.metric_inputs`, when present, must be an array of
  strings naming the aggregate inputs used by that feature.
- Feature-level provenance must override artifact-level provenance when a
  feature is derived from a narrower or different rowset than the artifact as a
  whole.
- `table_used` in provenance must remain compatible with the artifact-level
  `table_used`; conflicting values are hard failures when the feature is used as
  evidence.
- Generic 429/5xx rate features are eligible as crawler findings only when
  artifact-level `rowset_scope.population` or feature-level
  `feature_provenance.<feature>.rowset_scope.population` explicitly identifies
  `crawler`, `good_bot`, or `ai_crawler`.

Future schema revisions may introduce equivalent provenance fields, but the MVP
renderer must implement only the field names above so tests and report behavior
stay deterministic.

Renderer behavior before this refinement lands:

- `rate_429_delta_high` and `rate_5xx_delta_high` must not be rendered as
  crawler findings unless the MVP `rowset_scope` or `feature_provenance` fields
  prove a crawler, good-bot, or AI-crawler population.
- Without that MVP provenance, those generic 429/5xx features may appear only as
  context or as an evidence limit explaining that crawler-specific provenance is
  unavailable.
- The renderer must not treat free-form field names, feature names,
  `supporting_metrics`, or broad artifact `scope` text as sufficient proof for
  these generic crawler findings.

### `bot_control_review.v1` Window Metadata

Control-review producers, including `compare_posture.py` output for
`bot_control_review.v1`, should preserve explicit windows so the report can
show what "before", "after", and "expected" mean without inferring time ranges
from `change_time` alone. `compare_posture.py` and any other
`bot_control_review.v1` producers must be updated before control-review reports
can be considered complete.

Required reporting metadata:

- `before_window`;
- `after_window`;
- `expected_window` when expected values come from a time window;
- `expected_basis`, with one of `before_window`, `explicit_target`,
  `external_model`, or `unknown`, so the renderer knows whether a missing
  `expected_window` is a metadata gap;
- `scope` when the reviewed control applies to a host, property, path, ASN, or
  other bounded population.

Required producer changes:

- `bot_control_review.v1` should preserve `scope` when supplied.
- `bot_control_review.v1` should preserve `before_window` and `after_window`
  when supplied.
- `bot_control_review.v1` should preserve `expected_window` when expected
  values come from a time window.
- `bot_control_review.v1` should emit `expected_basis`. Use `before_window` when
  the producer uses the before period as the expected baseline, `explicit_target`
  when the caller supplied literal expected values, `external_model` when the
  expected values came from another model or forecast, and `unknown` only when
  the producer cannot determine the basis.
- References and examples for `bot_control_review.v1` should be updated
  alongside the producer changes.

Renderer behavior before these refinements land:

- Missing control-review windows are warnings, not silently inferred from
  `change_time`.
- If `expected_basis` is absent or `unknown` and target effects contain expected
  values, the renderer must warn that the expected-value basis is unavailable. If
  `expected_basis` is `before_window` or `external_model` and `expected_window`
  is absent, the renderer must warn that expected-window metadata is unavailable.
- A control-review report may render in degraded mode, but it must visibly mark
  unavailable window metadata in the report and in CLI warnings.

Example:

```json
{
  "schema_version": "bot_control_review.v1",
  "comparison_type": "post_change_vs_expected",
  "change_time": "2026-04-01T00:00:00Z",
  "target": {"policy_id": "policy-123"},
  "scope": {"request_host": "www.example.com"},
  "before_window": {"start": "2026-03-25T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
  "after_window": {"start": "2026-04-01T00:00:00Z", "end": "2026-04-08T00:00:00Z"},
  "expected_basis": "before_window",
  "expected_window": {
    "start": "2026-03-25T00:00:00Z",
    "end": "2026-04-01T00:00:00Z",
    "label": "expected_from_before_window"
  },
  "table_used": "bot_siem_summary_day",
  "target_effects": []
}
```

## Optional Timeseries Artifact

The existing artifacts are mostly current/baseline and entity-summary shaped.
If demos need trend lines, introduce an explicit pre-aggregated timeseries
artifact rather than forcing line charts out of non-timeseries data.

This is future support only. `bot_timeseries.v1` must not be accepted by the
MVP renderer for validation or rendering, even though this design sketches the
shape a later implementation should use.

Example:

```json
{
  "schema_version": "bot_timeseries.v1",
  "table_used": "bot_summary_hour",
  "scope": {"request_host": "www.example.com"},
  "time_column": "timestamp",
  "series": [
    {
      "name": "bot_share_pct",
      "unit": "percent",
      "points": [
        {"timestamp": "2026-04-01T00:00:00Z", "value": 31.2}
      ]
    }
  ],
  "interpretation_constraints": [
    "pre_aggregated_timeseries",
    "no_causal_claim",
    "llm_may_summarize_structured_evidence_only"
  ]
}
```

Hydrolix still creates the rows. The renderer only draws the series.

## Report Types

### Executive Posture

Audience: director and executive users.

Purpose:

- summarize posture movement;
- show current versus baseline changes;
- identify top entities that deserve follow-up;
- route investigation to the right team.

Required artifacts:

- `bot_posture_movement.v1`

Optional artifacts:

- `bot_scorecard_index.v1`
- `bot_mover_attribution.v1`
- `bot_scorecard_artifacts.v1`

Suggested sections:

- title and scope;
- executive summary;
- metric delta cards;
- current versus baseline bars;
- top scorecard ranking;
- confidence and evidence limits.

### SOC Triage

Audience: security and SOC users.

Purpose:

- prioritize risky entities;
- expose security evidence;
- show what drove movement;
- preserve caveats around aggregate attribution.

Minimum artifacts for MVP SOC triage:

- `bot_scorecard_index.v1`

This minimum supports a degraded ranking-only SOC report with visible warnings.
It is valid for MVP rendering, but it has no entity-level feature evidence and
therefore cannot render scorecard-dependent sections.

Artifacts required to render scorecard-dependent SOC sections:

- `bot_scorecard_artifacts.v1` that normalizes into a valid nested
  `bot_scorecard_index.v1` plus at least one compatible nested
  `bot_entity_scorecard.v1`; or
- `bot_scorecard_index.v1` plus one or more compatible
  `bot_entity_scorecard.v1` artifacts.

Optional artifacts:

- `bot_mover_attribution.v1`
- `bot_posture_movement.v1`

`bot_scorecard_artifacts.v1` satisfies this requirement only through valid
normalized children. Required-artifact validation for MVP SOC triage should
accept the minimum index-only set as degraded mode, and should require the
scorecard set above only before rendering scorecard-dependent sections. A
standalone `bot_scorecard_index.v1` can support top-risk ranking only; it is
insufficient for domain score matrix, security evidence notes, missing-feature
evidence, or confidence-reason rendering. Empty or malformed scorecard packets
do not satisfy scorecard-dependent sections. They fail closed only when those
sections require them and no other valid compatible scorecard set is available.

MVP section-selection behavior:

- Default SOC triage with only `bot_scorecard_index.v1` renders a ranking-only
  degraded report with explicit warnings.
- Default SOC triage with compatible scorecards renders scorecard-dependent
  sections for compatible scorecards only.
- The degraded report must omit domain matrix, security evidence notes,
  missing-feature evidence, and confidence-reason sections that require entity
  scorecards.
- It must not render empty evidence or matrix sections as if evidence existed.
- No CLI section-selection flag is required for the MVP.

Future section-selection behavior:

- When wrapper `sections` support is implemented in a future phase, explicitly
  requested scorecard-dependent sections should fail if only an index artifact
  is available.
- Until that future config exists, index-only SOC output should degrade by
  omission and visible warnings rather than by section-level request failures.

Suggested sections when SOC scorecard evidence is available:

- title and scope;
- top risky entities;
- scorecard ranking bars;
- domain score matrix;
- mover contribution bars;
- security evidence notes;
- confidence and evidence limits.

### Control Review

Audience: director, security, and operations users.

Purpose:

- review before/after target effects;
- compare after values to expected values when provided;
- show collateral checks and displacement checks;
- avoid unsupported causal claims.

Required artifacts:

- `bot_control_review.v1`

Optional artifacts:

- `bot_posture_movement.v1`
- `bot_mover_attribution.v1`

Suggested sections:

- title and scope;
- control review summary;
- before/after/expected bars;
- target effects;
- collateral checks;
- displacement checks;
- confidence and evidence limits.

### Scorecard Brief

Audience: analyst handoff or demo walkthrough.

Purpose:

- explain why one entity ranked high;
- show feature evidence and missing inputs;
- keep scoring deterministic.

Required artifacts:

- `bot_entity_scorecard.v1`

Optional artifacts:

- compatible `bot_scorecard_index.v1`

Suggested sections:

- title and entity identity;
- score and confidence;
- domain scores;
- feature evidence;
- not-evaluated features;
- artifact-provided `recommended_next_steps`.

Rules:

- Render `recommended_next_steps` from the artifact when present.
- Do not invent follow-up questions or next steps in the renderer.
- Analyst-authored follow-up questions may appear only when supplied through
  `analyst_notes`; they must be labeled as interpretation and cite artifact
  data sources where possible.

### Crawler Governance

Audience: SEO and crawler-governance users.

Purpose:

- summarize good-bot and AI-crawler posture;
- expose crawler rate limiting, 5xx exposure, and governance-surface gaps;
- separate verified crawler governance from bad-bot security triage;
- preserve caveats around missing crawler inputs.

Required artifacts:

- `bot_scorecard_artifacts.v1` that normalizes into at least one valid
  `bot_entity_scorecard.v1`; or
- one or more `bot_entity_scorecard.v1` artifacts.

Optional artifacts:

- `bot_scorecard_index.v1`
- `bot_posture_movement.v1`
- `bot_mover_attribution.v1`

Suggested sections:

- title and scope;
- crawler posture summary;
- crawler-governance feature evidence;
- affected crawler/entity ranking;
- good-bot rate-limit and error evidence;
- AI-crawler movement when provided by artifact fields;
- missing crawler inputs and confidence limits.

Rules:

- Use only scorecard domains and feature evidence already emitted by
  `scorecard.py`, especially `crawler_governance` features.
- Relevant crawler-governance evidence is limited to evaluated scorecard
  features in the `crawler_governance` domain:
  `rate_429_delta_high`, `rate_5xx_delta_high`, `good_bot_429_present`,
  `good_bot_error_rate_high`, `policy_surface_failure_present`, and
  `ai_crawler_growth_high`.
- `rate_429_delta_high` and `rate_5xx_delta_high` are generic rate-delta
  features because `scorecard.py` derives them from generic
  `current_rate_429_pct`/`baseline_rate_429_pct` and
  `current_rate_5xx_pct`/`baseline_rate_5xx_pct` inputs. Treat them as
  crawler-governance findings only when artifact-level `rowset_scope.population`
  or feature-level `feature_provenance.<feature>.rowset_scope.population` is
  `crawler`, `good_bot`, or `ai_crawler`.
- Until structured provenance exists, generic 429/5xx movement may be listed
  only as context or an evidence limit. It must not appear in the crawler
  posture summary, affected crawler/entity ranking, or crawler findings
  language, even when free-form feature metadata or `supporting_metrics` names
  mention crawler-like terms.
- Explicitly crawler-scoped features remain eligible when their inputs support
  the interpretation: `good_bot_429_present`, `good_bot_error_rate_high`,
  `policy_surface_failure_present`, and `ai_crawler_growth_high`.
- Relevant missing crawler-governance inputs are `not_evaluated_features` in
  the `crawler_governance` domain, including those same feature names and their
  `missing_inputs`.
- Optional posture or mover artifacts may support crawler narrative only when
  their artifact fields explicitly name crawler, good-bot, AI-crawler, 429, 5xx,
  or governance-surface metrics. The renderer must not reinterpret unrelated
  movement as crawler evidence.
- Do not invent crawler-health status when the relevant feature inputs appear
  in `not_evaluated_features`.
- Ranking should preserve `bot_scorecard_index.v1` order when an index is
  present, even if the section filters displayed evidence to crawler-related
  domains.
- The affected crawler/entity ranking should include only entities with
  evaluated relevant crawler-governance features. Preserve the relative index
  order for the included entities when an index exists.
- If no index exists, preserve normalized scorecard input order for
  crawler-specific entity lists and label the list as input order, not as a new
  crawler ranking.
- Do not include entities that have only unrelated scorecard domains in crawler
  findings. They may be mentioned only in evidence limits as available but
  non-relevant scorecards.
- If scorecards are present but none contain evaluated crawler-governance
  features, the report should render in degraded mode with a visible "no
  relevant crawler-governance evidence available" section and warnings. This is
  not evidence that crawler posture is safe.
- Missing relevant crawler feature inputs must be listed in evidence limits and
  stderr warnings, not treated as zero risk or healthy crawler behavior.

### Edge/Ops Impact

Audience: edge operations, performance, and platform users.

Purpose:

- summarize cache, origin, bandwidth, and query-string impact indicators;
- identify entities driving operational follow-up;
- distinguish operational symptoms from causal claims;
- preserve caveats around retained dimensions and aggregate attribution.

Required artifacts:

- `bot_scorecard_artifacts.v1` that normalizes into at least one valid
  `bot_entity_scorecard.v1`; or
- one or more `bot_entity_scorecard.v1` artifacts.

Optional artifacts:

- `bot_scorecard_index.v1`
- `bot_posture_movement.v1`
- `bot_mover_attribution.v1`

Suggested sections:

- title and scope;
- Edge/Ops impact summary;
- origin-impact and cache-busting feature evidence;
- affected entity ranking;
- mover contribution bars;
- cache/origin posture deltas when provided;
- missing operational inputs and confidence limits.

Rules:

- Use only scorecard domains and feature evidence already emitted by
  `scorecard.py`, especially `cache_busting`, `origin_impact`, and relevant
  movement features.
- Relevant Edge/Ops scorecard evidence is limited to evaluated features in
  `cache_busting` and `origin_impact`: `cache_miss_rate_high`,
  `cache_miss_delta_high`, `querystring_diversity_high`,
  `querystring_diversity_with_high_miss_rate`, `origin_p95_delta_high`, and
  `origin_cost_contribution_high`.
- Movement-domain evidence is relevant to Edge/Ops only when the artifact field
  names or supporting metrics explicitly identify an operational metric, such as
  cache miss rate, cache misses, origin p95/TTFB, origin cost, bandwidth,
  query-string diversity, or origin-facing traffic. Generic `new_entity`,
  request-volume, bot-share, or total-score movement is not operational impact
  evidence by itself.
- Relevant missing operational inputs are `not_evaluated_features` in
  `cache_busting` or `origin_impact`, plus movement features skipped because
  explicit operational metric fields were missing.
- Treat mover attribution as aggregate attribution, not root cause.
- Ranking should preserve `bot_scorecard_index.v1` order when an index is
  present, even if the section filters displayed evidence to operational
  domains.
- The affected Edge/Ops entity ranking should include only entities with
  evaluated relevant operational features. Preserve the relative index order for
  the included entities when an index exists.
- If no index exists, preserve normalized scorecard input order for
  Edge/Ops-specific entity lists and label the list as input order, not as a new
  operational ranking.
- Do not include entities that have only unrelated scorecard domains in Edge/Ops
  findings. They may be mentioned only in evidence limits as available but
  non-relevant scorecards.
- If scorecards are present but none contain evaluated Edge/Ops features, the
  report should render in degraded mode with a visible "no relevant Edge/Ops
  evidence available" section and warnings. This is not evidence that edge or
  origin impact is safe.
- Missing relevant operational feature inputs must be listed in evidence limits
  and stderr warnings, not treated as zero risk or healthy edge behavior.

## Visualization Types

### Metric Delta Cards

Source:

- `bot_posture_movement.v1`

Display:

- metric name;
- current value;
- baseline value;
- absolute delta;
- percent change;
- direction;
- confidence.

Rules:

- Do not recompute deltas.
- Use artifact-provided values only.
- Display missing unit as an empty or generic unit, not as percent.

### Current Versus Baseline Bars

Source:

- `bot_posture_movement.v1`

Display:

- one grouped bar pair per metric;
- current and baseline labels;
- direction and confidence in adjacent text.

Rules:

- Scale bars only for visual layout.
- Do not normalize values across unrelated units in a way that hides units.
- Prefer separate groups or labels when mixing counts, percentages, and
  milliseconds.

### Mover Contribution Bars

Source:

- `bot_mover_attribution.v1`

Display:

- horizontal bars by `contribution_pct` when available;
- fallback to `absolute_delta` with clear labeling;
- sorted descending by contribution or absolute delta.

Rules:

- Preserve artifact ordering when values tie.
- Include total delta when present.
- Show confidence per mover.

### Scorecard Ranking Bars

Source:

- `bot_scorecard_index.v1`
- `bot_entity_scorecard.v1` only as a no-index fallback display

Display:

- horizontal bars by deterministic score;
- entity label;
- entity type;
- confidence label.

Rules:

- Preserve `ranked_entities` order and displayed rank when a
  `bot_scorecard_index.v1` artifact is available.
- If rendering from raw `bot_entity_scorecard.v1` artifacts without an index,
  sort by score descending, then entity label only as deterministic display
  ordering. Label the output as "sorted by emitted score" or equivalent, not as
  a ranking.
- Display rank numbers and use ranking language only when a
  `bot_scorecard_index.v1` artifact supplies `ranked_entities`.
- Do not recompute score.
- Respect renderer row limits and disclose omitted rows according to the
  display-count rules in Reliability Rules.

### Domain Score Matrix

Source:

- `bot_entity_scorecard.v1` or `bot_scorecard_artifacts.v1`

Display:

- rows are entities;
- columns are scorecard domains;
- cells show score and intensity color.

Rules:

- A domain score of `0` means "no scored feature points for this domain," not
  automatically "unavailable."
- For the MVP, render numeric `domain_scores` exactly as emitted and list
  `not_evaluated_features` separately in evidence limits. Do not claim that a
  zero score proves the domain was fully evaluated unless existing artifact
  fields explicitly prove that status.
- Show a domain as unavailable only when existing artifact fields prove that all
  or the relevant domain features appear in `not_evaluated_features` due to
  missing inputs and no evaluated feature in that domain provides evidence.
- If a domain has evaluated zero-point or no-threshold-crossing evidence and
  also has missing inputs, render the numeric score and list the missing inputs
  separately in evidence limits.
- If the scorecard schema cannot distinguish evaluated-zero from missing inputs
  cleanly enough, keep the MVP behavior degraded: render the emitted numeric
  score, list missing inputs separately, and make the ambiguity visible in
  evidence limits. Richer per-domain evaluation status remains a future schema
  refinement, not an MVP prerequisite.
- Domain order should follow the scorecard artifact domain order when present.
- Limit rows for readability using the resolved display limit.

### Control Before/After/Expected Bars

Source:

- `bot_control_review.v1`

Display:

- grouped bars for before, after, and expected values;
- status label;
- confidence label.

Rules:

- Do not claim the control caused a change unless external change evidence is
  explicitly supplied outside the artifact.
- Preserve `status` from the artifact.

### Optional Timeseries Lines

Source:

- `bot_timeseries.v1`

MVP status:

- unsupported in MVP; inputs with `bot_timeseries.v1` must fail as an
  unsupported known future schema.

Display:

- line or area chart per series;
- timestamp axis;
- metric unit labels.

Rules:

- Only use explicit timeseries artifacts.
- Do not infer trend points from current/baseline artifacts.
- Avoid smoothing unless the artifact explicitly says the data is smoothed.

## Output Formats

### Markdown

Markdown output should be portable and dependency-free.

Markdown is the default CLI output format because it is suitable for stdout and
plain-text demos. HTML remains the primary full-MVP demo format when callers pass
`--format html`.

Use:

- headings;
- summary paragraphs;
- tables;
- bullet lists for confidence and missing evidence.

Avoid:

- embedded HTML where possible;
- separate image generation for the MVP.

Markdown escaping rules:

- Treat artifact strings and analyst-note fields as plain text, not Markdown.
- Escape Markdown metacharacters in user-controlled text before inserting it into
  headings, paragraphs, lists, or tables: backslash, backtick, asterisk,
  underscore, braces, brackets, parentheses, hash, plus, minus, period,
  exclamation mark, pipe, angle brackets, and ampersand.
- Replace line breaks inside table cells with spaces.
- Never render links, images, autolinks, inline HTML, or raw Markdown supplied by
  artifact labels or analyst notes.

### HTML

HTML output is the primary demo format for the demo MVP. The demo MVP is the
combined Phase 1 + Phase 2 deliverable; Phase 1 is only an intermediate
Markdown checkpoint.

Use:

- one self-contained `.html` file;
- inline CSS;
- inline SVG charts;
- no external fonts, scripts, images, or CDN assets;
- responsive but simple layout;
- accessible labels for chart values.

The HTML report should work by opening the file directly in a browser.

## Reliability Rules

- Reject unknown artifact schemas by default.
- Escape all user-controlled labels, including hosts, paths, query strings,
  policy IDs, entity names, and scope labels.
- Preserve schema-specific metadata as available:
  - all schemas: `schema_version`, `artifact_id`, `parent_artifact_id`,
    `table_used`, `scope`, `confidence`, `confidence_reasons`,
    `interpretation_constraints`;
  - `bot_posture_movement.v1`: `comparison_type`, `granularity`,
    `current_window`, `baseline_windows`;
  - `bot_mover_attribution.v1`: `comparison_type`, `granularity`, `dimension`,
    `metric`, `total_delta`, `total_delta_basis`;
  - `bot_control_review.v1`: `comparison_type`, `change_time`, `target`,
    `before_window`, `after_window`, `expected_window`, `expected_basis`;
  - `bot_entity_scorecard.v1`: `entity_type`, `entity`, `comparison_type`,
    `granularity`, `current_window`, `baseline_windows`, `band`,
    `primary_domain`;
  - `bot_scorecard_index.v1`: `comparison_type`, `current_window`,
    `baseline_windows`, rank/order in `ranked_entities`;
  - `bot_scorecard_artifacts.v1`: parent packet identity plus the nested
    `index` and `scorecards` source paths used during normalization.
- Apply metadata warnings per schema instead of using one generic missing-field
  rule:
  - posture and scorecard artifacts should warn on missing
    `current_window`/`baseline_windows` because those schemas describe
    current-versus-baseline state;
  - control-review artifacts should warn on missing `before_window` or
    `after_window`; they should warn on missing `expected_basis` when target
    effects contain expected values; they should warn on missing
    `expected_window` when `expected_basis` is `before_window` or
    `external_model`;
  - mover artifacts should warn on missing `dimension` or `metric`, but should
    not warn on missing windows unless the producer supplied one window and not
    the other;
  - `bot_scorecard_index.v1` alone should not warn about missing feature
    evidence fields because the index schema intentionally does not contain
    feature-level evidence.
- Render missing fields as unavailable.
- Render report scope from wrapper `scope_label` when it is present, while still
  preserving artifact `scope` metadata for validation, compatibility, and
  evidence. A wrapper `scope_label` is display text only and must not be used to
  fill, replace, reconcile, or prove artifact `scope`.
- When wrapper `scope_label` is absent, render artifact scope only when selected
  report artifacts have known, unambiguous `scope` metadata. If artifact scope
  is absent, unknown, or mixed across selected artifacts, render scope as
  unavailable or mixed and add a visible warning or evidence-limit entry that
  names the missing or conflicting scope metadata.
- Surface `not_evaluated_features`.
- Include an evidence-limits section in every report.
- Use deterministic ordering:
  - existing artifact rank/order for `bot_scorecard_index.v1`;
  - score descending, then entity label only as display ordering for generic
    scorecard bars from raw scorecards without an index, labeled as sorted by
    emitted score rather than as a ranking;
  - report-specific filtered scorecard lists, such as crawler governance and
    Edge/Ops, use their report-specific ordering rules and must not create new
    domain rankings by recomputing or summing features;
  - contribution descending, then mover label;
  - artifact order for otherwise tied sections.
- Do not recompute metrics, rates, confidence, or scores.
- Avoid causal language for movement-only artifacts.
- Preserve control-review framing as effectiveness review, not proof of cause.
- A domain score of `0` means "no scored feature points for this domain." It
  does not automatically mean the domain was unavailable.
- Render a domain as unavailable only when all or the relevant domain features
  appear in `not_evaluated_features` due to missing inputs and no evaluated
  feature in that domain provides evidence.
- If a domain has both evaluated zero-point or no-threshold-crossing evidence
  and missing inputs, render the numeric score and separately list missing
  inputs in evidence limits.
- If the existing scorecard schema cannot distinguish evaluated-zero from
  missing inputs cleanly enough for a domain, treat richer per-domain
  evaluation status as a future schema refinement and make the ambiguity
  visible in evidence limits.

### Producer Limit Metadata Contract

Do not hide row limits. The renderer may always disclose display counts from
the normalized artifact collection, such as "showing 10 of 12 artifacts
available to this report."

Producer-side limit metadata is optional only for producer outputs that do not
apply a limit before emitting artifacts or whose current schema cannot carry
packet-level producer metadata. If a producer applies a script-side or
query-side limit before emitting artifacts and emits a metadata-capable packet,
index, or wrapper, it must emit:

- `result_row_count`: rows or entities emitted by the producer;
- `producer_limit`: limit applied by the producer script or query;
- `result_truncated`: boolean indicating whether the producer limit truncated
  the emitted result set.

Upstream population metadata is optional and must be emitted only when known:

- `source_row_count`: full source population available to the producer or
  query;
- `total_ranked_entities`: total ranked population before the producer's
  emitted-artifact limit.

The renderer may say "showing 10 of 50 total entities" only when producer
metadata explicitly provides the total population or total ranked count. If
these fields are absent, the renderer must only disclose counts from the
emitted normalized artifact collection and must explicitly say the producer did
not provide full source-population metadata. Do not imply that the artifact
contains every omitted upstream entity.

Limited report inputs that need producer-limit disclosure should use
`bot_scorecard_artifacts.v1`, `bot_scorecard_index.v1`, or another future schema
that explicitly carries producer metadata. A bare raw list of
`bot_entity_scorecard.v1` artifacts has no packet-level location for
`producer_limit`, `result_row_count`, or `result_truncated`; the renderer treats
that list as the emitted known collection unless it is wrapped by a future
metadata-bearing schema. Do not describe bare scorecard-list output as
preserving producer-limit metadata.

Current `scorecard.py --limit` can truncate scorecards before the renderer sees
them. In this worktree, the default `bot_scorecard_artifacts.v1` output and
`--output index` emit `producer_limit`, `result_row_count`,
`result_truncated`, and `total_ranked_entities` for that path. The
`--output scorecards` shape emits a bare list and cannot preserve packet-level
producer-limit metadata. Full Hydrolix source counts remain optional unless the
producer actually knows them. For any producer output that does not emit the
metadata above, the renderer must treat the emitted scorecards and
`ranked_entities` as the known collection, not as proof of the full upstream
population.

## Flexibility Rules

Flexibility should come from declarative report intent, not arbitrary template
logic.

Allowed flexibility:

- report type;
- title;
- scope label;
- row limit;
- Markdown versus HTML output;
- optional analyst notes.

Future flexibility:

- wrapper-level `sections` config for explicit section selection.
- wrapper-level artifact subset selection with explicit selector syntax and
  selection timing.

Artifact subsets are out of scope for the MVP. The MVP renderer consumes the
entire normalized artifact collection for the resolved report type. Display row
limits may hide rows from a section, but they do not select source artifacts,
change primary artifact choice, or remove artifacts from evidence-limit
accounting. If a report type requires a single primary artifact and the
normalized collection contains multiple valid candidates, the renderer must fail
closed with an ambiguity error. Future artifact subset support must define
selectors before normalization or after normalization, not both, and must make
selection timing visible enough for analyst-note citations to remain stable.

Avoid for MVP:

- custom template languages;
- arbitrary expressions;
- custom JavaScript;
- user-defined chart plugins;
- renderer-side SQL snippets;
- renderer-side joins across raw query outputs.

Possible future section config:

```json
{
  "sections": [
    {"type": "summary"},
    {"type": "metric_deltas"},
    {"type": "scorecard_ranking", "limit": 10},
    {"type": "domain_matrix", "limit": 10},
    {"type": "evidence_limits"}
  ]
}
```

## Error Handling

The renderer should fail closed when report integrity is at risk.

Hard failures:

- invalid JSON;
- top-level JSON shape is not a single known artifact object, a non-empty raw
  artifact array, or a `bot_report_input.v1` wrapper;
- unknown schema without `--allow-unknown`;
- `bot_timeseries.v1` in MVP input, because it is a known future schema but
  unsupported for MVP validation and rendering;
- known schema with incompatible top-level shape;
- raw array input without CLI `--report-type`;
- wrapper `report_type` conflicts with CLI `--report-type`;
- CLI `--limit` is zero, negative, or not an integer;
- requested report type has no usable required artifact after resolving report
  type and normalizing artifacts; for SOC triage, a standalone
  `bot_scorecard_index.v1` is a usable degraded-mode minimum;
- a selected report or selected scorecard-dependent section depends on a
  scorecard packet that is empty or malformed and does not normalize into valid
  required child artifacts;
- required artifacts have incompatible `scope`, `current_window`,
  `baseline_windows`, `comparison_type`, or `table_used` values, or standalone
  or cross-packet pairings have unknown required compatibility metadata for a
  relationship that must be proven; unknown shared metadata on children from the
  same `bot_scorecard_artifacts.v1` packet is a warning after `entity_type` and
  entity match;
- scorecard-dependent report sections would pair a `bot_scorecard_index.v1`
  with `bot_entity_scorecard.v1` artifacts whose `entity_type`, entity key, or
  compatibility metadata does not match;
- duplicate wrapper `artifact_id` values;
- explicit artifact IDs using reserved generated child suffixes such as
  `#index` or `#scorecard-N`;
- duplicate artifact IDs in the normalized collection after scorecard-packet
  child generation;
- ambiguous multiple same-schema artifacts when the selected report type
  requires a single primary artifact;
- unsupported MVP artifact subset selection or any selector that would make
  primary artifact selection ambiguous;
- structurally invalid `analyst_notes`, including missing or non-string `text`,
  unsupported `author_type`, malformed `data_sources`, non-object data-source
  entries, missing/non-string `json_pointer`, malformed JSON Pointer syntax, or
  unresolved citation targets;
- output path cannot be written.

Warnings:

- CLI `--title` or `--limit` overrides a wrapper value;
- optional artifact missing;
- chart skipped because required fields are unavailable;
- rows omitted because of display limit;
- producer did not provide full source-population metadata when a display limit
  or omitted-row disclosure needs a source-total count;
- artifact lacks metadata required by that schema's warning policy;
- artifact lacks confidence metadata in fields where that schema normally
  carries confidence;
- exact duplicate artifact body without explicit IDs was ignored because it
  could not affect report meaning, ranking, or citations;
- multiple same-schema artifacts were all rendered because the report type can
  consume them unambiguously;
- optional artifacts are missing compatibility metadata or conflict with the
  primary artifact set and are omitted from combined sections;
- extra empty or malformed scorecard packets are present but are not needed to
  satisfy the selected report because another valid artifact set does so;
- SOC triage has only `bot_scorecard_index.v1` and renders a degraded
  ranking-only report without scorecard-dependent sections;
- crawler governance or Edge/Ops report has scorecards but no eligible
  evaluated relevant domain evidence and renders a no-relevant-evidence
  section;
- relevant crawler or Edge/Ops feature inputs are missing and listed in
  evidence limits;
- generic crawler 429/5xx features lack structured provenance and are listed
  only as context or evidence limits;
- analyst note has no data-source references;

Warnings should be rendered in the report and written to stderr for CLI use.

## Testing Plan

Add tests to `tests/test_skill_scripts.py` or a new report-specific test file.

Core tests:

- raw known artifact input renders successfully only when `--report-type` is
  supplied or single-artifact report inference is unambiguous;
- raw top-level artifact array normalizes in input order and requires
  `--report-type`;
- wrapper input renders successfully;
- omitted CLI `--format` defaults to Markdown output;
- wrapper and CLI matching `report_type` values render successfully;
- wrapper and CLI conflicting `report_type` values fail closed before required
  artifact validation;
- required-artifact validation uses the resolved report type after
  normalization;
- CLI `--title` overrides wrapper `title` with a visible diagnostic warning;
- CLI `--limit` overrides wrapper `limit` with a visible diagnostic warning and
  does not affect required-artifact validation;
- CLI `--limit` zero, negative, and non-integer values fail closed;
- when CLI `--limit` and wrapper `limit` are absent, report-type default display
  limits are applied consistently to limited sections;
- wrapper `scope_label` renders as presentation scope text and does not
  override conflicting or missing artifact `scope` metadata for compatibility
  or evidence-limit handling;
- absent wrapper `scope_label` renders unambiguous artifact `scope` metadata,
  and renders unavailable or mixed scope with visible warnings or evidence
  limits when artifact scope metadata is absent, unknown, or mixed;
- `bot_scorecard_artifacts.v1` decomposes into nested index and scorecards;
- `bot_scorecard_artifacts.v1` satisfies scorecard-dependent reports only when
  it contains at least one valid nested `bot_entity_scorecard.v1`;
- SOC scorecard-dependent sections accept a scorecard packet only when it
  normalizes into a valid nested or standalone `bot_scorecard_index.v1` plus
  compatible scorecards;
- empty, missing, or malformed scorecard-packet children fail closed when they
  are required to satisfy the selected report or selected scorecard-dependent
  section and no valid alternative artifact set is available;
- extra empty or malformed scorecard packets do not fail rendering when another
  valid artifact set satisfies the selected report, and are surfaced in
  warnings or evidence limits;
- unknown schema is rejected;
- `--allow-unknown` permits unknown schemas but lists them as skipped;
- `bot_timeseries.v1` is rejected in MVP as a known future schema unsupported
  for validation and rendering, and `--allow-unknown` does not make it
  renderable;
- Markdown and HTML escape entity labels, scope labels, paths, query strings,
  analyst-note text, analyst-note titles, and citation labels;
- Markdown rendering treats artifact strings and analyst-note fields as plain
  text and never renders user-supplied links, images, inline HTML, or raw
  Markdown syntax;
- analyst notes are escaped, labeled as LLM-based interpretation, and rendered
  with data-source citations;
- analyst-note validation fails closed for missing `text`, non-string `text`,
  missing or unsupported `author_type`, malformed `data_sources`, non-object
  data-source entries, missing or non-string `json_pointer`, malformed JSON
  Pointer syntax, and unresolved citation targets;
- analyst notes without data sources produce warnings;
- analyst-note citations resolve by `artifact_id`, verify optional
  `schema_version` consistency, fail on mismatch, and fail rather than resolving
  ambiguous schema-only citations;
- analyst notes can cite normalized child artifacts from
  `bot_scorecard_artifacts.v1`;
- scorecard ranking preserves `bot_scorecard_index.v1` rank order;
- scorecard ranking from raw scorecards uses deterministic fallback sorting
  only when no index is available and labels the output as display ordering,
  not ranking;
- missing evidence appears in the evidence-limits section;
- domain matrix renders numeric `0` scores as evaluated zero-point evidence
  unless `not_evaluated_features` shows the domain is unavailable;
- domains with both evaluated zero evidence and missing inputs render the
  numeric score plus evidence-limit warnings;
- confidence reasons are rendered;
- row limits disclose displayed versus available artifact rows without claiming
  a total source count when producer metadata is absent;
- row limits disclose "10 of 50" style totals only when producer metadata
  supplies the total;
- control-review window metadata is rendered;
- control-review artifacts missing before/after windows produce warnings;
- control-review artifacts with expected values but missing or `unknown`
  `expected_basis` produce warnings;
- control-review artifacts with `expected_basis: "before_window"` or
  `expected_basis: "external_model"` and missing `expected_window` produce
  warnings;
- control-review output uses review language rather than causal proof language;
- movement-only output avoids phrases such as "caused by" and "proves";
- warnings are written to stderr as well as rendered in the report;
- unwritable output paths fail with a non-zero exit and stderr error;
- duplicate `artifact_id` values fail closed;
- reserved explicit artifact ID suffixes such as `#index` and `#scorecard-N`
  fail closed;
- normalized artifact ID collisions, including collisions between generated
  scorecard-packet child IDs and explicit raw artifact IDs, fail closed;
- exact duplicate artifact bodies without explicit IDs are deduplicated with a
  warning when dedupe cannot affect report meaning, ranking, or citations;
- exact duplicate artifact bodies fail when they have explicit IDs, are
  referenced by analyst notes, or dedupe could affect report selection, ranking,
  or citations;
- multiple same-schema artifacts are handled per report type, including
  ambiguous primary-artifact failure;
- cross-artifact compatibility fails closed when required artifacts disagree on
  `scope`, `current_window`, `baseline_windows`, `comparison_type`, or
  `table_used`, or when a standalone or cross-packet relationship requires known
  compatibility metadata and that metadata is unknown;
- scorecard index and scorecards are related only when `entity_type`, entity
  key, and the applicable packet or standalone compatibility rule match;
- scorecard index and scorecard children from the same
  `bot_scorecard_artifacts.v1` packet can render scorecard-dependent sections
  when entity keys match, even if shared compatibility metadata is unavailable,
  and the missing metadata appears as warnings and evidence limits;
- standalone or cross-packet scorecard index and scorecard artifacts fail
  compatibility when `scope`, `current_window`, `baseline_windows`, or
  `table_used` is unknown, or when `comparison_type` is known on only one side;
- an index from one host or current window is never paired with scorecards from
  another host or current window;
- optional posture, mover, or control artifacts with missing or conflicting
  compatibility metadata produce degraded warnings and are omitted from combined
  sections;
- MVP SOC triage with only `bot_scorecard_index.v1` degrades to ranking-only
  output with visible warnings and never renders empty matrix or evidence
  sections as if evidence existed;
- crawler governance with scorecards but no eligible evaluated
  `crawler_governance` evidence renders a degraded no-relevant-evidence report
  rather than unrelated crawler findings;
- crawler governance does not render `rate_429_delta_high` or
  `rate_5xx_delta_high` as crawler findings when structured rowset or feature
  provenance is absent, even when feature names or supporting metrics mention
  crawler-like terms;
- crawler governance may render `rate_429_delta_high` and
  `rate_5xx_delta_high` as crawler findings only when artifact-level
  `rowset_scope.population` or feature-level
  `feature_provenance.<feature>.rowset_scope.population` is `crawler`,
  `good_bot`, or `ai_crawler`; otherwise it lists the generic movement only as
  context or an evidence limit;
- crawler governance keeps explicitly crawler-scoped features such as
  `good_bot_429_present`, `good_bot_error_rate_high`,
  `policy_surface_failure_present`, and `ai_crawler_growth_high` eligible when
  their required inputs support that interpretation;
- crawler governance lists missing `crawler_governance` feature inputs in
  evidence limits and does not treat them as safe;
- Edge/Ops impact with scorecards but no evaluated `cache_busting` or
  `origin_impact` evidence renders a degraded no-relevant-evidence report rather
  than unrelated operational findings;
- Edge/Ops impact lists missing operational feature inputs in evidence limits
  and does not treat them as safe;
- crawler governance and Edge/Ops entity lists preserve index order after
  filtering to relevant evaluated evidence, and use input order with clear
  labeling when no index exists;
- MVP artifact subset selectors are rejected or treated as unsupported so
  primary artifact selection remains deterministic;
- Scorecard Brief renders artifact-provided `recommended_next_steps` and does
  not invent follow-up questions outside labeled analyst notes;
- Scorecard Brief may use a related index through the applicable same-packet or
  standalone compatibility rule;
- producers that apply limits before emitting artifacts include
  `producer_limit`, `result_row_count`, and `result_truncated` when they emit a
  metadata-capable packet, index, or wrapper; source totals remain optional
  unless known;
- `scorecard.py --limit` default `bot_scorecard_artifacts.v1` output and
  `--output index` include `producer_limit`, `result_row_count`,
  `result_truncated`, and `total_ranked_entities`;
- `scorecard.py --limit --output scorecards` emits a bare scorecard list, is not
  expected to include packet-level producer-limit metadata, and is treated by
  the renderer as the emitted known collection rather than evidence of the full
  upstream population.

Future section-config tests:

- explicitly requested scorecard-dependent sections fail when only
  `bot_scorecard_index.v1` is available;
- wrapper `sections` config applies section ordering and per-section limits
  only after section selection is implemented.
- future artifact subset selectors apply at the documented selection point and
  keep analyst-note citation IDs stable.

Snapshot-style tests may be useful for small HTML/SVG fragments, but avoid
large brittle full-file snapshots unless the renderer output is intentionally
locked down.

## Implementation Phases

These phases describe target capability and remaining work. This worktree
already contains an initial `render_report.py` and `scorecard.py` limit
metadata, so phase language below is about completion against this design rather
than file existence. Phase 0 is a separate schema-refinement track. The demo MVP
is complete after Phase 2. Phase 1 is an intermediate checkpoint that proves
parsing, validation, Markdown rendering, and report semantics before HTML is
considered complete.

### Phase 0: Schema Refinement Prerequisites

- Update `compare_posture.py` and any other `bot_control_review.v1` producers
  to preserve `scope`, `before_window`, `after_window`, `expected_basis`, and
  time-window-derived `expected_window` values when supplied.
- Update `bot_control_review.v1` references and examples alongside producer
  changes.
- Add the MVP `rowset_scope` and `feature_provenance` fields so generic 429/5xx
  rate features can be safely distinguished between crawler-specific and
  all-traffic rowsets.
- Keep required producer-side limit metadata on metadata-capable outputs from
  scripts that apply limits before emitting artifacts. `scorecard.py --limit`
  already emits `producer_limit`, `result_row_count`, `result_truncated`, and
  `total_ranked_entities` for the default `bot_scorecard_artifacts.v1` output
  and `--output index` in this worktree. Bare `--output scorecards` lists do not
  carry packet-level producer-limit metadata.
- Keep upstream totals such as `source_row_count` and
  `total_ranked_entities` optional and emit them only when the producer knows
  them. `scorecard.py` can still report whether its own `--limit` truncated
  emitted scorecards without knowing the full Hydrolix source population.
- Track richer per-domain evaluation status as a future schema refinement if
  current scorecard output cannot distinguish evaluated-zero from missing-input
  domains cleanly enough.

Renderer implementation can continue before Phase 0 is complete by emitting
degraded warnings and visibly unavailable metadata. Complete control-review
window precision requires Phase 0 control metadata, and generic crawler 429/5xx
findings require Phase 0 `rowset_scope` or `feature_provenance` metadata.

### Phase 1: Markdown Renderer Checkpoint

- Harden the existing `render_report.py` against this design.
- Parse wrapper, raw single-artifact, and raw artifact-array inputs.
- Resolve report type, title, and display limit using the precedence rules in
  this design, including fail-closed report-type conflicts.
- Normalize `bot_scorecard_artifacts.v1` into nested index and scorecard
  artifacts for report selection.
- Enforce cross-artifact compatibility before combining index, scorecard,
  posture, mover, and control artifacts.
- Render Markdown sections for executive posture, SOC triage, control review,
  scorecard brief, crawler governance, and Edge/Ops impact.
- Validate analyst notes fail-closed for structural errors, then render valid
  notes as explicitly labeled interpretation with cited data sources and
  warnings for missing or unresolved sources.
- Include evidence limits and confidence details.
- Add unit tests for parsing, schema handling, ordering, and escaping.
- Render degraded warnings for missing control-review windows and absent
  source-population metadata, and keep generic crawler 429/5xx evidence out of
  crawler findings unless the MVP `rowset_scope` or `feature_provenance` fields
  prove a crawler, good-bot, or AI-crawler population.

### Phase 2: Self-Contained HTML

- Add HTML output mode.
- Add inline CSS.
- Add SVG primitives for:
  - metric delta cards;
  - current versus baseline bars;
  - scorecard ranking bars;
  - mover contribution bars;
  - domain score matrix;
  - control before/after/expected bars.
- Add tests for HTML escaping and chart presence.

### Phase 3: Demo Polish

- Add sample artifact fixtures for demos.
- Add example commands.
- Tune CSS for readable projector and screen-share output.
- Add optional `bot_timeseries.v1` support only if demos need trend lines.

## Deferred Decisions

These are intentionally outside the accepted MVP contract:

- Decide whether sample demo artifacts live in the repo or are generated from
  tests to avoid stale examples.
- Decide whether `render_report.py` should write warnings into a sidecar JSON
  file for automation, beyond stderr plus rendered warning sections.
- Decide whether HTML reports should include print-specific CSS for PDF export.
- Decide whether the renderer should expose a `--strict` mode that fails on
  missing optional artifacts for scripted demos.
- Decide whether future scorecard provenance schemas should support equivalent
  field names beyond the MVP `rowset_scope` and `feature_provenance` contract.

## Recommended MVP

Finish the smallest renderer path that supports a strong demo. This recommended
demo MVP spans Phase 1 and Phase 2, with Phase 1 serving as a Markdown-only
checkpoint and Phase 2 adding the primary HTML demo format:

1. `render_report.py` with Markdown and HTML output.
2. Wrapper input support plus raw single-artifact and raw-array convenience with
   explicit report-type resolution.
3. Automatic `bot_scorecard_artifacts.v1` decomposition into valid nested index
   and scorecard children.
4. Cross-artifact compatibility validation before combining related artifacts.
5. Explicitly labeled analyst notes with fail-closed structural validation and
   artifact-field citations.
6. Explicit control-review before/after/expected window rendering when metadata
   is provided, with degraded warnings when it is absent.
7. Executive posture, SOC triage, control review, scorecard brief, crawler
   governance, and Edge/Ops impact report types.
8. Inline SVG charts for bars and matrices only.
9. Strict schema validation, escaping, stable ordering, evidence limits, and
   confidence rendering.

Defer custom templates, JavaScript charts, PDF generation, and timeseries lines
until a demo or customer workflow proves they are needed.
