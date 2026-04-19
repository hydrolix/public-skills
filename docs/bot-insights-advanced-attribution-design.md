# Bot Insights Advanced Attribution Engine Design

This document designs the next-generation attribution engine for the
`bot-insights` skill. It is design-only; it does not require local scripts to
query Hydrolix, open database clients, read credentials, or process raw large
rowsets.

The engine should stay mechanical and explainable. It attributes aggregate
current-versus-baseline movement to grouped dimensions. It does not make causal
claims, classify traffic with opaque models, or recommend mitigations directly.

## Existing State

`bot-insights` already has a basic posture and mover attribution MVP in
`skills/bot-insights/scripts/compare_posture.py`.

The current script emits:

- `bot_posture_movement.v1`
- `bot_mover_attribution.v1`
- `bot_control_review.v1`

The existing `compare_movers(...)` behavior computes:

- current and baseline values;
- absolute delta;
- guarded percentage change using the same baseline formula as the references;
- increase, decrease, and no-change direction labels;
- contribution percentage;
- confidence labels and machine-readable reasons;
- interpretation constraints.

The current MVP stops at simple, mostly single-dimension mover attribution. It
does not model multi-dimension keys as a first-class output, does not classify
new and disappeared entities explicitly, and does not strongly guard
contribution math against limited rowsets.

`skills/bot-insights/scripts/scorecard.py` already has stricter patterns that
advanced attribution should reuse conceptually:

- missing inputs are reported, not treated as safe;
- mixed row shapes are rejected;
- contribution percentages are only auto-computed when metadata proves
  complete scope with `rowset_complete: true` or
  `contribution_basis: "complete_scope"`.

## Schema Direction

Keep `bot_mover_attribution.v1` as the legacy/simple mover schema.

Policy:

- Keep `bot_mover_attribution.v1` available for existing simple workflows.
- Freeze it except for bug fixes and small compatibility improvements.
- Do not extend it into the advanced attribution engine.
- Document it as a simple single-dimension packet emitted by
  `compare_posture.py`.
- Make `bot_attribution_report.v1` the target schema for new attribution work.

This avoids churn in existing docs, examples, and consumers while allowing the
advanced engine to have stricter guards and a simpler future-facing contract.

## V1 Scope

### Overall V1 Capabilities

The list below describes the complete v1 product surface after all v1 phases
are done. The phase acceptance criteria in the implementation plan are
authoritative for what may ship in v1a, v1b, and v1c. In particular, high
confidence is unavailable until a reviewed skill-controlled direct-MCP runner
exists, and advanced scorecard export is unavailable until v1c scorecard
hardening is complete.

- Top movers for one selected metric and one dimension set per report.
- Single-dimension attribution, such as `client_asn`, `request_host`,
  `bot_class`, `ai_category`, or `request_path_norm`.
- Composite dimension attribution that is backed by one retained summary-table
  dimension set, such as `request_host + bot_class`,
  `request_path_norm + bot_class`, `request_path_norm + asn_type`,
  `client_asn + ai_category`, or `client_asn + bot_class`.
- Direction labels: `increase`, `decrease`, `no_change`.
- Presence lifecycle labels for emitted mover rows: `new`, `disappeared`,
  `existing`, and `not_evaluated`, based on count/support and
  period-presence evidence rather than arbitrary selected metric values.
  Default v1 excludes unsafe one-sided rows from ranked `movers[]`, totals,
  and buckets. `not_evaluated` is used only for emitted rows whose selected
  metric values are present but lifecycle/support cannot be evaluated, such as
  non-volume metrics without support fields, one-sided sparse support below
  `min_count` with candidate flags, or behind a future explicit
  include-not-evaluated option.
  `absent` is a v1 bucket-only concept for trusted zero/zero support rows; it
  is not emitted in ranked `movers[]`.
- Support-change labels, separate from presence lifecycle, such as
  `support_increase`, `support_decrease`, `support_unchanged`, and
  `not_evaluated`. `support_zero_both` is paired with the v1 bucket-only
  `absent` concept and is not emitted in ranked `movers[]`.
- Separate sparse candidate flags for low-support new or disappeared entities.
- Contribution math only for additive metrics when complete-scope evidence is
  present. Non-additive metrics may be compared as movers in v1, but
  contribution must be withheld unless v1 has reviewed aggregation semantics
  for that exact metric in its allowlist.
- Explicit limitations when contribution math is withheld or the result is
  limited.
- Scorecard handoff metadata that separates preserve-safe provided
  contribution from compute-safe complete-rowset contribution, but only as a
  v1c explicit export after scorecard hardening. V1a and v1b must not expose
  scorecard export.
- Confidence labels and machine-readable reasons.
- Explicit interpretation constraints.
- Compatibility with MCP result JSON, saved JSON, and pasted aggregate JSON.
- Summary-table-first SQL templates with no database clients.

### Should Have

- Support for already-combined rows and period-split rows.
- Strict rejection of mixed row shapes in one run.
- Input metadata echoing for table, windows, baseline method, scope, metric,
  row count, limit, and contribution basis.
- `not_evaluated_components` for missing or unsafe calculations.
- Summary-dimension fit reporting that distinguishes summary-backed dimensions
  from raw-table fallback or unsupported combinations.

### Later

- Multiple metrics per report.
- Multiple dimension sets per report.
- Parent-child rollups, such as ASN results with nested path movers.
- Metric and dimension registry files.
- Timeline reconstruction.
- Seasonal or rolling baselines beyond provided current/baseline aggregate
  rows.
- Optional displacement candidate summary based on offsetting positive and
  negative movers.
- Optional high-cardinality summaries for ASN-plus-path attribution, such as
  `bot_agg_asn_path_hour` or `bot_agg_asn_path_day`, after catalog cardinality
  validation.

### Out Of Scope

- Hydrolix clients, credentials, or query execution.
- Raw request-level row processing at scale.
- Opaque machine learning, clustering, or statistical classification.
- Causal claims.
- Mitigation recommendations.
- Treating missing inputs as zero-risk, zero-impact, or zero.
- Contribution percentages for non-additive metrics unless the metric has
  explicit reviewed semantics in the v1 allowlist.

## Recommended Implementation Shape

Create a new script:

```text
skills/bot-insights/scripts/attribution.py
```

Do not grow `compare_posture.py` into the advanced attribution engine.

Rationale:

- `compare_posture.py` already has three stable responsibilities: posture,
  simple movers, and control review.
- Advanced attribution needs stricter row normalization, complete-scope
  contribution guards, presence lifecycle labels, support-change labels,
  composite dimension keys, limitations, and explicit evidence/assertion
  provenance metadata.
- Keeping the advanced engine separate reduces compatibility risk for
  `bot_mover_attribution.v1`.

`attribution.py` should be dependency-light and use the Python standard library
only for v1.

### V1 Components And Skill-Controlled Boundary

V1 has three responsibilities that must stay separate:

- Local normalizer: `skills/bot-insights/scripts/attribution.py` reads
  aggregate JSON from file/stdin, MCP result JSON, pasted JSON, or a wrapper
  object; validates row shape, metric aliases, dimensions, baseline method,
  contribution evidence, zero-fill evidence, lifecycle support, confidence, and
  scorecard handoff safety in phases where scorecard export is enabled; and
  emits `bot_attribution_report.v1`. In v1c, reviewed export code may construct
  an internal `bot_scorecard_input.v1` handoff object and pass it directly to
  hardened scorecard generation with `scorecard_trusted_context`; the
  normalizer must not expose it as a reusable standalone artifact. It does not
  query Hydrolix, read credentials, open database clients, or treat JSON fields
  as skill-controlled evidence.
- Skill-controlled SQL/template generator: a bundled reviewed component,
  referred to here as `bot-insights-attribution-sql`, builds SQL/templates and
  provenance metadata from reviewed metric semantics, requested dimensions,
  report scope, current and baseline windows, baseline method, and Hydrolix
  table metadata.
  It is a first-class v1 component because production SQL for Hydrolix summary
  tables must use table metadata and exact merge expressions for
  aggregate-state columns. It does not execute Hydrolix data queries and does
  not own direct MCP result handling.
- Skill-controlled runtime wrapper: a host/plugin integration layer, referred
  to here as `bot-insights-attribution-runner`, owns any direct-MCP
  high-confidence path. It selects a reviewed template, initiates the MCP tool
  call through the skill/runtime control plane, receives the MCP result object
  directly in memory, maps that result into normalizer rows, computes the
  canonical `result_digest`, constructs the skill-controlled
  `trusted_context`, and calls
  `normalize_attribution(input_doc, trusted_context=...)` in the same Python
  process. This wrapper must not be a database client and must not receive its
  trusted inputs through caller-editable JSON, saved files, pasted text, CLI
  flags, or notebooks.

The `trusted_context` parameter name is retained as API shorthand, but in this
design it means an internal skill-controlled execution context. It is not
cryptographic trust, tamper resistance, process isolation, authorization, or a
proof of origin. It is only the normalizer's way to distinguish evidence passed
directly by reviewed skill code in the same workflow from caller-controlled
public JSON assertions.

The skill-controlled SQL/template generator's v1 responsibilities are:

- validate the selected table and retained dimensions before generating SQL;
- resolve metric aliases through the v1 reviewed allowlist;
- inspect or receive trusted Hydrolix table metadata and use the exact
  metadata-derived merge expression for `AggregateFunction` and
  `SimpleAggregateFunction` columns;
- generate SQL/templates that compute current, normalized baseline,
  support-count fields, complete-scope denominators, per-row contribution, and
  lifecycle-friendly zero-fill rows before any output limit;
- attach provenance for the template, metric expression, denominator
  expression, zero-fill strategy, scope, windows, baseline method, source-limit
  stage, query fingerprint, and digest-relevant metadata. The generator must
  not compute or attach `result_digest`; only the direct-MCP runtime wrapper can
  compute the canonical `result_digest` after it maps the actual result payload;
- return evidence payloads only to the skill-controlled runtime wrapper or to
  tests that model that wrapper boundary.

The skill-controlled runtime wrapper's v1b direct-MCP responsibilities are:

1. Select a supported template, table, columns, metric, dimensions, scope,
   windows, and Hydrolix metadata source through reviewed code.
2. Request table metadata through the MCP metadata tool, then pass that
   metadata to the generator so merge expressions and `metadata_fingerprint`
   are derived before query execution. Checked-in metadata fixtures may be used
   by generator and unit tests to exercise SQL generation and metadata
   fingerprinting, but fixture-backed invocation does not by itself unlock
   high confidence.
3. Request the Hydrolix query through the MCP query tool selected by the
   wrapper. The wrapper receives the direct MCP result object in memory through
   the tool call return value, not through user-edited text, a saved file, a
   pasted JSON block, notebook output, or CLI flags.
4. Map the MCP result columns and rows into the normalizer's input row shape,
   reject duplicate or ambiguous result columns before trust evaluation, and
   compute the canonical `result_digest` over the full `digest_payload_v1`
   report-input contract.
5. Construct `trusted_context` from reviewed template state, bound parameters,
   metadata identity, query fingerprint, result digest, and candidate evidence
   objects that the normalizer will validate after it recomputes the digest.
6. Call `normalize_attribution(input_doc, trusted_context=trusted_context)`
   in-process. The CLI path and saved JSON path must always pass
   `trusted_context=None`.

The required v1b baseline for this repo is generated SQL/templates,
provenance metadata, and standalone normalization. The direct-MCP
high-confidence wrapper is in scope for v1b only if this repo can also provide
that host/plugin integration as reviewed code with tests that prove the MCP
result object is passed directly in memory. If v1b cannot provide that
integration, v1b is explicitly capped at generated SQL/templates plus
standalone normalization at medium confidence or lower. In that capped v1b,
direct-MCP high confidence, trusted lifecycle absence, and trusted contribution
from MCP result objects move to a later phase, and saved or pasted MCP JSON is
handled exactly like any other caller-supplied aggregate JSON.

If a wrapper cannot guarantee that direct, non-user-editable MCP result path,
the MCP payload is treated exactly like saved or pasted MCP JSON and is capped
below `high`. Pasted or saved MCP JSON remains caller-controlled because the
normalizer cannot distinguish a faithful copy from edited data or copied
evidence fields. In a skill-controlled workflow, the wrapper may call the local
normalizer in-process with:

```text
normalize_attribution(input_doc, trusted_context=trusted_context)
```

where `input_doc` contains rows or MCP result rows, and `trusted_context` is an
in-memory object or dict built only by reviewed wrapper code. The normalizer
must never accept `trusted_context` from file/stdin JSON, pasted JSON, CLI
flags, arbitrary metadata sidecars, notebook output, or any caller-editable
JSON field. Standalone CLI mode always calls
`normalize_attribution(input_doc, trusted_context=None)`.
File, stdin, saved JSON, pasted JSON, notebook output, ordinary MCP
`columns`/`rows` JSON, and caller-editable wrapper JSON must always call or
behave as `normalize_attribution(input_doc, trusted_context=None)`.
Reviewed wrapper code must construct the context from its own template
selection, bound parameters, trusted metadata, and mapped result object; it
must not deserialize or copy a caller-provided `trusted_context` value into the
normalizer.

The distinction is explicit:

- Skill-controlled execution: reviewed skill code selects and renders
  SQL/templates, receives MCP metadata and query output directly, maps columns
  and rows, computes the digest, attaches internal evidence/context, and calls
  the normalizer in-process with `trusted_context`.
- Public or standalone execution: file, stdin, pasted, saved, notebook, or
  ordinary `columns`/`rows` JSON is caller-controlled input. Its fields are
  treated as assertions, even when they contain values such as
  `evidence_source: "trusted_template_generator"`, and this path is capped
  below `high`.

In v1, high confidence, trusted contribution, trusted lifecycle absence, and
scorecard export safety can be unlocked only by reviewed skill-controlled
direct-MCP wrapper code that receives the MCP query result object directly in
memory, constructs `trusted_context` itself, and invokes the normalizer
in-process. This direct-MCP `trusted_context` requirement is universal for
every high-confidence report and every high-confidence mover, including
positive/positive rows that do not emit contribution or absence claims.
Standalone, file, stdin, saved, pasted, notebook, fixture-backed, and public
MCP JSON paths can reach only `low` or `medium` confidence in v1. The generator
can produce provenance, metadata fingerprints, query fingerprints, and trusted
evidence payloads for the wrapper, but without this direct-MCP wrapper boundary
those facts are assertion-only for normalizer trust decisions. Checked-in
metadata fixtures are test and generator aids only; they may support SQL
generation tests and metadata fingerprint tests, but saved JSON,
fixture-backed CLI input, pasted JSON, and standalone/file/stdin workflows
remain capped below `high` unless the actual query result flows through the
direct-MCP wrapper boundary.

The skill-controlled context is payload-owned, not ID-owned. It must carry the
evidence objects themselves and the digest binding those objects to the actual
result payload. IDs, field names, or booleans in the input document are never
enough to unlock high confidence, trusted contribution, trusted lifecycle
absence, or scorecard export safety. The minimum `trusted_context` interface
is:

Evidence examples that demonstrate trusted Hydrolix summary-table SQL use
aggregate-state column notation. In those examples, `selected_columns` names
the actual aggregate-state metadata column, such as `sum(cnt_all)`, and
`merge_expressions` wraps that exact name with the metadata-derived merge
function, such as `sumMerge(\`sum(cnt_all)\`)`. Examples that use plain numeric
helper columns must not pair those helpers with aggregate-state merge
expressions.

```json
{
  "trusted_generator_invocation": true,
  "generator_name": "bot-insights-attribution-sql",
  "generator_version": "1.0.0",
  "wrapper_name": "bot-insights-attribution-runner",
  "wrapper_version": "1.0.0",
  "template_id": "complete_scope_single_dimension_v1",
  "query_fingerprint": "sha256:...",
  "result_digest": "sha256:...",
  "result_origin": "direct_mcp_tool_output",
  "metadata_origin": "direct_hydrolix_table_metadata",
  "selected_table": "bot_summary_day",
  "selected_columns": ["timestamp", "client_asn", "request_host", "sum(cnt_all)"],
  "metadata_fingerprint": "sha256:...",
  "metadata_retrieval_identity": "hydrolix-mcp:get_table_info:bot_summary_day:<retrieved_at>",
  "merge_expressions": {
    "sum(cnt_all)": "sumMerge(`sum(cnt_all)`)"
  },
  "trusted_evidence": [
    {
      "evidence_id": "complete-scope-pre-limit-v1",
      "evidence_type": "complete_scope_pre_limit_evidence",
      "applies_to": {"scope": "report"},
      "evidence_source": "trusted_template_generator",
      "generator_name": "bot-insights-attribution-sql",
      "generator_version": "1.0.0",
      "template_id": "complete_scope_single_dimension_v1",
      "query_fingerprint": "sha256:...",
      "result_digest": "sha256:...",
      "metric": "requests",
      "metric_expression": "<metadata_derived_metric_expr>",
      "metric_semantics_reviewed": true,
      "dimensions": ["client_asn"],
      "grouped_dimensions": ["client_asn"],
      "selected_table": "bot_summary_day",
      "selected_columns": ["timestamp", "client_asn", "request_host", "sum(cnt_all)"],
      "metadata_origin": "direct_hydrolix_table_metadata",
      "metadata_fingerprint": "sha256:...",
      "metadata_retrieval_identity": "hydrolix-mcp:get_table_info:bot_summary_day:<retrieved_at>",
      "merge_expressions": {
        "sum(cnt_all)": "sumMerge(`sum(cnt_all)`)"
      },
      "current_window": {"start": "2026-03-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
      "baseline_windows": [
        {"start": "2026-02-01T00:00:00Z", "end": "2026-03-01T00:00:00Z", "label": "previous_month"}
      ],
      "baseline_method": "single_previous_window",
      "baseline_value_semantic": "duration_normalized_to_current_window",
      "baseline_normalization": {
        "method": "scale_baseline_to_current_window_duration",
        "current_duration_seconds": 2678400,
        "baseline_duration_seconds": 2419200,
        "factor": 1.107143,
        "factor_expression": "current_duration_seconds / baseline_duration_seconds",
        "applies_to": ["baseline"]
      },
      "scope": {"request_host": "www.example.com"},
      "applied_scope_filters": {"request_host": "www.example.com"},
      "scope_matches_report": true,
      "windows_match_report": true,
      "baseline_method_matches_report": true,
      "computed_over_complete_grouped_scope": true,
      "computed_before_output_limit": true,
	    "limit_stage": "after_denominator"
    }
  ],
    {
      "evidence_id": "zero-fill-full-scope-join-v1",
      "evidence_type": "zero_fill_evidence",
      "applies_to": {"scope": "report"},
      "evidence_source": "trusted_template_generator",
      "generator_name": "bot-insights-attribution-sql",
      "generator_version": "1.0.0",
      "template_id": "full_scope_joined_pre_limit_v1",
      "query_fingerprint": "sha256:...",
      "result_digest": "sha256:...",
      "period_value_trust": {
        "current": "trusted_full_scope_join",
        "baseline": "trusted_full_scope_join"
      },
      "metric": "requests",
      "metric_expression": "<metadata_derived_metric_expr>",
      "metric_semantics_reviewed": true,
      "dimensions": ["client_asn"],
      "grouped_dimensions": ["client_asn"],
      "grouped_scope_complete": true,
      "selected_table": "bot_summary_day",
      "selected_columns": ["timestamp", "client_asn", "request_host", "sum(cnt_all)"],
      "metadata_origin": "direct_hydrolix_table_metadata",
      "metadata_fingerprint": "sha256:...",
      "metadata_retrieval_identity": "hydrolix-mcp:get_table_info:bot_summary_day:<retrieved_at>",
      "merge_expressions": {
        "sum(cnt_all)": "sumMerge(`sum(cnt_all)`)"
      },
      "current_window": {"start": "2026-03-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
      "baseline_windows": [
        {"start": "2026-02-01T00:00:00Z", "end": "2026-03-01T00:00:00Z", "label": "previous_month"}
      ],
      "baseline_method": "single_previous_window",
      "baseline_value_semantic": "duration_normalized_to_current_window",
      "baseline_normalization": {
        "method": "scale_baseline_to_current_window_duration",
        "current_duration_seconds": 2678400,
        "baseline_duration_seconds": 2419200,
        "factor": 1.107143,
        "factor_expression": "current_duration_seconds / baseline_duration_seconds",
        "applies_to": ["baseline"]
      },
      "scope": {"request_host": "www.example.com"},
      "applied_scope_filters": {"request_host": "www.example.com"},
      "scope_matches_report": true,
      "windows_match_report": true,
      "baseline_method_matches_report": true,
      "full_scope_joined_grouped_rowset": true,
      "computed_before_output_limit": true
    }
  ]
}
```

`trusted_context.trusted_evidence` is a list, not a singleton object keyed by
evidence type. Each object must have a stable `evidence_id`, an
`evidence_type`, and an `applies_to` selector. `applies_to: {"scope": "report"}`
means the evidence applies to the whole normalized report. Row-specific evidence
must use a deterministic selector such as
`applies_to: {"row_key": {"client_asn": "12345"}}`, with keys matching the
requested `dimensions`. The normalizer filters this list by `evidence_type`,
validates `evidence_id` uniqueness, validates `applies_to` against the report or
row key, and then applies the basis-specific rules for that evidence type.

Trust-unlocking evidence in `input_doc` is ignored unless a matching typed
evidence object is present under `trusted_context.trusted_evidence`, has matching
`query_fingerprint` and `result_digest`, matches the relevant `applies_to`
selector, and passes all basis-specific validation. The normalizer may echo
input-side evidence-like fields only as caller assertions in `input_assertions`,
`debug`, or raw input echo sections.

Standalone `attribution.py` file/stdin mode has no such control-plane object.
Therefore standalone `attribution.py` must never emit `confidence: "high"`,
trusted `contribution_pct`, trusted lifecycle absence, or
`scorecard_export_safe: true` from user-supplied JSON alone, even when that JSON
contains fields named `evidence_source: "trusted_template_generator"`.
Standalone CLI, saved-file JSON, pasted JSON, and plain stdin JSON may still
produce useful partial reports, but those reports are capped at low or medium
confidence depending on their internal consistency and metadata completeness.

The v1 skill-controlled boundary is an accident-prevention and workflow
boundary. It is not cryptographic proof, tamper resistance, or a process
isolation boundary.
The library entrypoint must enforce the trusted-context shape, evidence payload
validation, and result digest binding so ordinary CLI/file/stdin/paste paths
cannot self-attest. The model does not attempt to defend against arbitrary
in-process callers; a Python caller can construct objects that look like
`trusted_context`. Consumers must never treat `confidence: "high"` as proof
that the data came from Hydrolix or from an unmodified query result. Portable
self-attesting JSON artifacts, subprocess isolation, or a stronger local
authorization model are explicitly v2-or-later features.

## Input Contract

The preferred input is a JSON object. This is the canonical complete
high-confidence example shape for a `complete_scope_pre_limit` input. It is
high-confidence eligible only when matching typed evidence objects are attached
by the reviewed direct-MCP wrapper through in-memory `trusted_context`, not
merely generated, saved, pasted, or checked in as JSON. The normalizer treats
this input-side evidence as a caller assertion unless matching evidence payloads
are present in `trusted_context.trusted_evidence`, match the relevant
`evidence_type` and `applies_to` selector, and are bound to the recomputed
`result_digest`:

```json
{
  "schema_version": "bot_attribution_input.v1",
  "comparison_type": "month_over_month",
  "granularity": "day",
  "table_used": "bot_agg_path_day",
  "summary_table_used": true,
  "scope": {"request_host": "www.example.com"},
  "current_window": {"start": "2026-03-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
  "baseline_windows": [
    {"start": "2026-02-01T00:00:00Z", "end": "2026-03-01T00:00:00Z", "label": "previous_month"}
  ],
  "baseline_method": "single_previous_window",
  "baseline_value_semantic": "duration_normalized_to_current_window",
  "baseline_normalization": {
    "method": "scale_baseline_to_current_window_duration",
    "current_duration_seconds": 2678400,
    "baseline_duration_seconds": 2419200,
    "factor": 1.107143,
    "factor_expression": "current_duration_seconds / baseline_duration_seconds",
    "applies_to": ["baseline"]
  },
  "metric": "requests",
  "caller_metric_kind_assertion": "additive_count",
  "dimensions": ["request_path_norm", "bot_class"],
  "row_shape": "combined",
  "rowset_complete": false,
  "source_limit_applied": false,
  "output_limit_applied": true,
  "output_limit": 50,
  "contribution_basis": "complete_scope_pre_limit",
  "complete_scope_total_abs_delta": 160000,
  "input_assertions": {
    "evidence": [
      {
        "evidence_id": "complete-scope-composite-dimension-v1",
        "evidence_type": "complete_scope_pre_limit_evidence",
        "applies_to": {"scope": "report"},
        "evidence_source": "trusted_template_generator",
        "generator_name": "bot-insights-attribution-sql",
        "generator_version": "1.0.0",
        "template_id": "complete_scope_composite_dimension_v1",
        "query_fingerprint": "sha256:...",
        "result_digest": "sha256:...",
        "metric": "requests",
        "denominator_expression": "sum(abs(current_requests - baseline_requests)) over ()",
        "denominator_basis": "sum_abs_delta",
        "metric_expression": "<metadata_derived_metric_expr>",
        "metric_semantics_reviewed": true,
        "selected_table": "bot_agg_path_day",
        "selected_columns": [
          "timestamp",
          "request_host",
          "request_path_norm",
          "bot_class",
          "sum(cnt_all)"
        ],
        "metadata_origin": "direct_hydrolix_table_metadata",
        "metadata_fingerprint": "sha256:...",
        "metadata_retrieval_identity": "hydrolix-mcp:get_table_info:bot_agg_path_day:<retrieved_at>",
        "merge_expressions": {
          "sum(cnt_all)": "sumMerge(`sum(cnt_all)`)"
        },
        "dimensions": ["request_path_norm", "bot_class"],
        "grouped_dimensions": ["request_path_norm", "bot_class"],
        "scope": {"request_host": "www.example.com"},
        "applied_scope_filters": {"request_host": "www.example.com"},
        "current_window": {"start": "2026-03-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
        "baseline_windows": [
          {"start": "2026-02-01T00:00:00Z", "end": "2026-03-01T00:00:00Z", "label": "previous_month"}
        ],
        "baseline_method": "single_previous_window",
        "baseline_value_semantic": "duration_normalized_to_current_window",
        "baseline_normalization": {
          "method": "scale_baseline_to_current_window_duration",
          "current_duration_seconds": 2678400,
          "baseline_duration_seconds": 2419200,
          "factor": 1.107143,
          "factor_expression": "dateDiff('second', current_start, current_end) / dateDiff('second', baseline_start, baseline_end)",
          "applies_to": ["baseline"]
        },
        "scope_matches_report": true,
        "windows_match_report": true,
        "baseline_method_matches_report": true,
        "computed_over_complete_grouped_scope": true,
        "computed_before_output_limit": true,
        "source_limit_applied_before_denominator": false,
        "pre_denominator_filter_applied": false,
        "limit_stage": "after_denominator"
      }
    ]
  },
  "rows": [
    {
      "request_path_norm": "/api/search",
      "bot_class": "good",
      "current_requests": 64000,
      "baseline_raw_requests": 10000,
      "baseline_requests": 11071.43,
      "current_support_raw": 64000,
      "baseline_support_raw": 10000,
      "contribution_pct": 33.08
    }
  ]
}
```

Minimum input contract for a useful report:

- `metric`, supplied by top-level input, metadata sidecar, or CLI
  `--metric`. V1 must not infer the selected metric when multiple current or
  baseline metric-like columns are present; ambiguous or missing metric
  selection fails with `metric_input_missing` or `ambiguous_metric_input`.
- `dimensions`, supplied by top-level input, metadata sidecar, CLI
  `--dimensions`, or deterministic MCP column inference under the MCP rules
  below. Inferred dimensions are confidence-capped and must be echoed with
  `dimensions_inferred`.
- `rows`, or MCP-style `columns` plus `rows` that can be mapped to rows.

Required for high confidence:

- valid in-memory `trusted_context` constructed by the reviewed direct-MCP
  runtime wrapper for this invocation. This is required for every high report
  and high mover in v1, regardless of whether the report emits complete-scope
  contribution, lifecycle absence, or only positive/positive existing rows.
- `comparison_type`
- `granularity`
- `table_used`
- `summary_table_used`
- selected table metadata identity: `selected_table`, `selected_columns`,
  `metadata_origin`, `metadata_fingerprint`, and
  `metadata_retrieval_identity` for high-confidence production evidence.
  `metadata_fixture_identity` is allowed only for checked-in generator or unit
  test fixtures and does not unlock high confidence outside the reviewed
  direct-MCP wrapper boundary.
- `scope`
- `current_window`
- `baseline_windows`
- `baseline_method`
- `baseline_value_semantic`
- `rowset_complete`
- `contribution_basis`
- trusted provenance evidence for any complete-scope contribution, lifecycle
  absence, or rowset-completeness claim used by the report
- `query_fingerprint` and `result_digest` in the trusted context for any
  trust-unlocking evidence
- trusted normalization metadata when `baseline_value_semantic` is any
  duration-normalized or reduced-window semantic
- `source_limit_applied`
- `output_limit_applied`

Caller-supplied `metric_kind` is an assertion/debug hint only and is not
required for a useful report. Normalized output may include `metric_kind`, but
that value must be derived from the reviewed metric allowlist and alias map
after `metric` has been resolved. Caller-supplied `metric_kind` must be echoed
only as `caller_metric_kind_assertion` or in debug metadata when useful. It is
not evidence that a metric supports contribution math, duplicate aggregation,
or lifecycle support.

`rowset_complete` describes the rows supplied to the local script. It may be
`false` when Hydrolix already computed complete-scope contribution values before
applying a final output limit. `source_limit_applied` describes whether rows were
limited before contribution totals were computed. `output_limit_applied`
describes whether a final result-size limit was applied after complete-scope
calculations.

### Skill-Controlled Provenance

The local script accepts saved JSON, MCP JSON, and pasted aggregate JSON. It
intentionally does not query Hydrolix, read credentials, or inspect raw rowsets.
Therefore caller-supplied booleans such as `scope_matches_report`,
`windows_match_report`, `computed_over_complete_grouped_scope`,
`rowset_complete`, and `source_limit_applied_before_denominator: false` are
assertions unless they are emitted through the v1 skill-controlled boundary.

The concrete v1 skill-controlled boundary is process/control-plane based, not
JSON-field based. Editable JSON fields never unlock trust by themselves:

- Skill-controlled in v1: evidence attached by reviewed direct-MCP runtime
  wrapper code during the same in-process workflow. The wrapper must receive
  the MCP result object directly in memory, map it, compute the digest,
  construct `trusted_context` itself, and call
  `normalize_attribution(input_doc, trusted_context=trusted_context)`.
  Generator output or another wrapper can participate only when it is inside
  this direct-MCP boundary.
- Untrusted assertions: arbitrary files, stdin, pasted JSON, saved JSON,
  notebook output, and plain MCP `columns`/`rows` JSON. These remain assertions
  even when they contain fields named `evidence_source:
  "trusted_template_generator"` unless the invocation path itself is trusted.
- Standalone CLI cap: file/stdin mode for `attribution.py` always treats
  provenance fields inside the input document as assertions. It can emit a
  useful partial report, but it cannot emit high confidence, trusted
  contribution, trusted zero-fill/lifecycle absence, or a safe scorecard export.
  Well-formed standalone JSON may reach medium confidence only for calculations
  that do not depend on trusted complete-scope, zero-fill, or scorecard
  evidence.
- Not implemented in v1: portable self-attesting evidence for standalone JSON
  files. A generated file that is later pasted or read from disk is not
  skill-controlled by itself. A checked-in metadata fixture may support
  generator tests and metadata fingerprint tests, but fixture-backed or saved
  JSON invocation remains assertion-only unless the actual query result flows
  through the direct-MCP wrapper boundary. Signed evidence for portable JSON
  artifacts is deferred to v2 or later.

V1 should describe trust-unlocking facts as normalized evidence that crossed
this skill-controlled boundary and passed field validation. Input-side metadata
should be named or interpreted as `*_assertion` unless it arrived through the
skill-controlled boundary. The script may echo caller assertions for debugging,
but caller assertions must not unlock high confidence, trusted
`contribution_pct`, trusted lifecycle absence, or compute-safe scorecard export.
Confidence is an analytical quality label based on internally controlled
calculation evidence. It is not proof of origin, authorization, or tamper
resistance. If skill-controlled calculation evidence is absent for an invocation
path, any user-supplied provenance metadata must cap report and mover confidence
below `high`.

Normalizer trust precedence is fail-closed:

- Treat every evidence, provenance, completeness, contribution, zero-fill,
  lifecycle, and scorecard-safety field inside `input_doc` as a caller
  assertion by default.
- Use only typed list entries under `trusted_context.trusted_evidence` for
  trust-unlocking decisions. Each entry must include unique `evidence_id`,
  recognized `evidence_type`, and an `applies_to` selector for the report or
  row key.
- Require `trusted_context.trusted_generator_invocation: true`, a recognized
  generator name/version, `template_id`, `query_fingerprint`, `result_origin`,
  `metadata_origin`, `metadata_fingerprint`, selected table/columns, and
  `result_digest` before considering trusted evidence.
- Validate that every trusted evidence object used for trust unlocking has the
  same `query_fingerprint` and `result_digest` as the trusted context.
- Validate trusted evidence fields against the report's metric, grouped
  dimensions, scope, current window, baseline windows, baseline method,
  baseline value semantic, normalization metadata, denominator identity, and
  limit stage before using the evidence.
- For zero-fill or absence evidence, validate nested `period_value_trust` inside
  the matching `evidence_type: "zero_fill_evidence"` object; caller-side copies
  outside `trusted_context.trusted_evidence` are assertions only.
- Downgrade to partial output if trusted evidence is missing, mismatched,
  stale, malformed, caller-supplied only, or not bound to the recomputed result
  digest.
- Record specific limitations such as `trusted_context_missing`,
  `trusted_context_invalid`, `trusted_context_digest_mismatch`,
  `trusted_evidence_missing`, `trusted_evidence_mismatch`,
  `query_fingerprint_missing`, or `result_digest_missing`.

Metadata precedence and conflicts are deterministic:

- CLI flags define the requested report contract for standalone runs. They may
  supply missing `metric`, `dimensions`, `scope`, `current_window`,
  `baseline_windows`, `baseline_method`, `baseline_value_semantic`, `limit`,
  and `min_count`, but they must not silently override a different value in
  trusted evidence or canonical top-level input.
- Trusted context is the only trust-unlocking source. When present and valid,
  it must match the report contract selected from CLI flags and input fields.
  It does not bypass conflict validation.
- Canonical top-level fields in `input_doc` outrank metadata sidecars for
  ordinary report values. Metadata sidecars outrank row-level repeated fields.
  MCP columns and row fields are data fields, not provenance, unless the
  wrapper maps them into a canonical top-level field and validates conflicts.
- Row-level fields may provide per-row values such as `current`, `baseline`,
  support counts, denominators, and supplied `contribution_pct`, but row-level
  metadata must not override report-level core fields.
- Core-field conflicts are fatal for high-confidence output and normally fatal
  input errors when they affect row normalization. Core fields are `metric`,
  `dimensions`, `scope`, `current_window`, `baseline_windows`,
  `baseline_method`, `baseline_value_semantic`,
  `baseline_normalization`, `contribution_basis`, selected table,
  selected columns, metadata fingerprint, denominator identity, and limit
  stage.
- If the conflicting field is optional debug metadata, preserve at most one
  value under `input_assertions` and add a warning limitation. If the conflict
  affects a trust-unlocking claim, ignore the evidence, cap confidence below
  `high`, and add `metadata_conflict` plus the basis-specific limitation such
  as `trusted_evidence_mismatch` or `complete_scope_not_proven`.
- Repeated MCP denominator columns may promote to top-level only when every
  returned row agrees and any existing top-level denominator is identical after
  numeric canonicalization. A mismatch invalidates the contribution evidence.
- Caller `metric_kind` never wins a conflict. The normalized `metric_kind`
  comes from the reviewed metric allowlist; the caller value is stored only as
  an assertion if it differs.

The wrapper must compute the deterministic v1 `result_digest` after column
mapping and before normalizer evidence validation. In v1, `result_digest` is
not a rows-only digest: it is the SHA-256 digest of a pre-trust canonical
`digest_payload_v1` report-input contract, including report metadata, selected
table metadata identity, mapped columns, and mapped rows.

Digest construction is validation-order sensitive and must not be circular. The
canonical digest payload is built from normalized input fields after alias
resolution, defaulting, MCP/result column mapping, selected table metadata
identity supplied by the wrapper or input contract, and numeric/timestamp
canonicalization. It must not copy values from
`trusted_context.trusted_evidence`, and trusted context must not repair,
override, or fill canonical fields during digest construction. After that
pre-trust payload is built, the normalizer independently recomputes the digest
and compares it with `trusted_context.result_digest` and each trust-unlocking
evidence object's digest. If they do not match, the normalizer must ignore
trusted evidence, downgrade the relevant calculations, and emit
`trusted_context_digest_mismatch`.

Digest payload contract:

- The digest schema is `digest_payload_v1`. The canonical payload must include
  `"digest_schema_version": "digest_payload_v1"`; canonical key sorting then
  determines serialized field order.
- The digest format is exactly `sha256:<64 lowercase hex characters>`, where
  the hex value is SHA-256 over the UTF-8 bytes of the canonical JSON payload.
- Canonical JSON uses sorted object keys, UTF-8, no insignificant whitespace,
  and arrays only where order is semantically defined below. Do not include
  Python object identity, memory address, local filesystem path, filename,
  mtime, or wrapper object identity.
- Included top-level fields are the canonical selected `metric`, derived
  normalized `metric_kind`, `dimensions` in report order, `scope`,
  `current_window`, `baseline_windows`, `baseline_method`,
  `baseline_value_semantic`, `baseline_normalization`, `row_shape`,
  `contribution_basis`, denominator fields, `source_limit_applied`,
  `output_limit_applied`, `output_limit`, `limit_stage`, selected table
  metadata identity, mapped columns, and mapped rows.
- Included mapped row fields are the dimension values for every requested
  dimension, `period` for period-split input, canonical `current`,
  canonical `baseline`, any retained raw input aliases needed to validate the
  semantic such as `baseline_raw_requests`, `current_support_raw`,
  `baseline_support_raw`, optional display support such as
  `baseline_support_normalized`, row-level
  `complete_scope_total_abs_delta`, and supplied `contribution_pct`. Extra
  unmapped source columns and evidence objects are excluded unless the wrapper
  maps a non-evidence value into one of these canonical input fields.
- Selected table metadata identity includes `selected_table`,
  `selected_columns`, `metadata_origin`, `metadata_fingerprint`,
  `metadata_retrieval_identity`, and `merge_expressions` for high-confidence
  production evidence. Checked-in test fixtures may use
  `metadata_fixture_identity` in fixture digest payloads, but that identity
  does not unlock high confidence for standalone/file/stdin/saved/pasted JSON.
  For summary tables, `merge_expressions` must identify the exact
  metadata-derived expression used for every selected aggregate-state metric or
  support column.
- Field precedence for the digest is pre-trust input precedence: explicit
  top-level canonical fields, then metadata sidecar fields, then mapped row or
  MCP column fields. CLI flags may select missing requested report-contract
  fields for standalone runs, but they must not silently override conflicting
  input. Trusted context values are excluded from digest construction; after the
  digest is recomputed, trusted context must match the canonical report contract
  and digest or be ignored for trust-unlocking decisions.
- Row ordering is canonicalized by sorting mapped rows by the normalized
  dimension values in `dimensions` order using stable string comparison with
  nulls last, then by `period` when present (`current` before `baseline`),
  then by the canonical row JSON string as a final tie-breaker. A wrapper may
  preserve original order for display, but the digest must use this canonical
  order.
- Missing and null are distinct. Missing optional fields are omitted from the
  canonical payload. Present JSON `null` is serialized as `null` and remains
  part of the digest. A missing required digest field invalidates the trusted
  context instead of being converted to null.
- Timestamps must normalize to RFC 3339 UTC with seconds precision and `Z`,
  for example `2026-03-01T00:00:00Z`. Offsets must be converted to UTC.
  Ambiguous local timestamps, partial timestamps, leap seconds, and timezone
  names that cannot be resolved deterministically are invalid for
  high-confidence evidence.
- Numbers must be finite decimals. The parser must reject non-standard JSON
  numeric values such as `NaN`, `Infinity`, and `-Infinity`, and must reject
  any in-process float that is not finite before digest calculation.
  Implementations should parse JSON numbers into `Decimal` or an equivalent
  decimal representation rather than relying on binary float formatting.
- Decimal canonical form uses no leading plus sign, no exponent, and no
  unnecessary leading zeros. Negative zero canonicalizes to `0`.
- `digest_payload_v1` precision is independent from public report display
  precision. Digest-canonical value-class decimals, including selected metric
  values, display support values included in the digest, deltas, denominators,
  and normalization factors, are rounded half-up to exactly six decimal places
  and serialized with exactly six fractional digits, for example
  `11071.430000` and `1.107143`. Digest-canonical percentage-class decimals,
  including `pct_change` and `contribution_pct`, are rounded half-up to exactly
  two decimal places and serialized with exactly two fractional digits, for
  example `33.08`. Integer support counts remain integers. Trailing zeros are
  retained in the digest payload when required by the precision class.
- Public `bot_attribution_report.v1` JSON may use display precision that trims
  insignificant trailing zeros and may show fewer decimals, such as
  `baseline: 11071.43`, while the digest payload canonicalizes the same value
  as `11071.430000`. Public examples may likewise show
  `baseline_normalization.factor: 1.107143`; the digest payload uses the
  six-decimal canonical value `1.107143`.
- Derived numeric fields must be rounded to digest-canonical precision before
  consistency checks and before digest calculation. The expected contribution
  percentage is computed from the digest-canonical `absolute_delta` and
  `complete_scope_total_abs_delta`, then rounded to two decimals before
  applying `contribution_pct_tolerance_pp`. Public display may use the same
  rounded value with trailing zeros trimmed.
- Float input that cannot round-trip to a finite decimal under these rules
  must be rejected for trusted evidence. Caller-side metadata-poor input may be
  degraded to low confidence when a non-core display field cannot be
  canonicalized, but core metric, support, denominator, contribution, and
  window fields are fatal when non-canonical.

`query_fingerprint` and `result_digest` are distinct and both are required for
high confidence. `query_fingerprint` identifies the reviewed generated
SQL/template, bound parameters, selected table, selected columns, metadata
fingerprint, metadata origin, merge expressions, and retrieval identity used
to build the query. Generator tests may use fixture identity in the same
fingerprint slot, but fixture identity alone is not high-confidence-unlocking.
`result_digest` binds trusted evidence to the canonical mapped report-input
contract after mapping, including report metadata, selected table metadata
identity, mapped columns, and mapped rows. A query fingerprint without a result
digest proves only query identity, not the payload being normalized. A result
digest without a query fingerprint proves only payload binding, not that the
payload came from a reviewed template and Hydrolix table metadata.

`metadata_fingerprint` is a schema fingerprint over the Hydrolix table metadata
used by the generator. For v1 it should be SHA-256 over canonical JSON
containing the selected table name, table summary/non-summary classification,
selected column names, selected column types/categories, aggregate
`merge_function` values, alias/default expressions used by the template, and
the metadata source identity. The high-confidence production metadata source
identity is `metadata_retrieval_identity` for live MCP metadata retrieval.
`metadata_fixture_identity` may appear in checked-in generator/unit test
fixtures and metadata fingerprint tests, but fixture-backed standalone,
file/stdin, saved, pasted, or ordinary MCP JSON invocation remains capped below
high. Trusted evidence must name the same `metadata_fingerprint`,
`selected_table`, `selected_columns`, and `merge_expressions` that were
included in the `query_fingerprint`; mismatches invalidate high confidence.

Accepted `evidence_source` values:

- `trusted_template_generator`: emitted by a reviewed SQL/template generator
  bundled with the skill or another reviewed generator, and accepted only when
  the current invocation path is inside the direct-MCP v1 skill-controlled
  boundary.
  Outside that boundary, generator provenance is assertion-only for normalizer
  trust decisions.
- `caller_assertion`: supplied by a caller or wrapper without a trusted
  generator identity or without a skill-controlled invocation path. Useful for
  display and debugging, but not trusted evidence.
- `manual_paste`: pasted aggregate rows or hand-authored metadata. This is
  metadata-poor by default and should normally cap confidence at `low`.

All trusted evidence metadata should identify both the calculation and its
provenance. The common identity fields look like this; each basis-specific
evidence object below adds the required validation fields for contribution,
rowset completeness, or absence:

```json
{
  "evidence_id": "complete-scope-single-dimension-v1",
  "evidence_type": "complete_scope_pre_limit_evidence",
  "applies_to": {"scope": "report"},
  "evidence_source": "trusted_template_generator",
  "generator_name": "bot-insights-attribution-sql",
  "generator_version": "1.0.0",
  "template_id": "complete_scope_single_dimension_v1",
  "query_fingerprint": "sha256:...",
  "result_digest": "sha256:...",
  "denominator_expression": "sum(abs(current_requests - baseline_requests)) over ()",
  "metric_expression": "<metadata_derived_metric_expr>",
  "selected_table": "bot_summary_day",
  "selected_columns": ["timestamp", "client_asn", "request_host", "sum(cnt_all)"],
  "metadata_origin": "direct_hydrolix_table_metadata",
  "metadata_fingerprint": "sha256:...",
  "metadata_retrieval_identity": "hydrolix-mcp:get_table_info:bot_summary_day:<retrieved_at>",
  "merge_expressions": {
    "sum(cnt_all)": "sumMerge(`sum(cnt_all)`)"
  },
  "grouped_dimensions": ["client_asn"],
  "applied_scope_filters": {"request_host": "www.example.com"},
  "current_window": {"start": "2026-03-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
  "baseline_windows": [
    {"start": "2026-02-01T00:00:00Z", "end": "2026-03-01T00:00:00Z", "label": "previous_month"}
  ],
  "baseline_method": "single_previous_window",
  "baseline_value_semantic": "duration_normalized_to_current_window",
  "baseline_normalization": {
    "method": "scale_baseline_to_current_window_duration",
    "factor": 1.107143,
    "factor_expression": "current_duration_seconds / baseline_duration_seconds",
    "applies_to": ["baseline"]
  },
  "limit_stage": "after_denominator"
}
```

These identity fields are required for every trust-unlocking evidence object,
including complete-rowset, complete-scope pre-limit, provided contribution,
zero-fill, duplicate aggregation, and raw fallback coverage evidence. Evidence
that omits a unique `evidence_id`, recognized `evidence_type`, valid
`applies_to`, `metadata_fingerprint`, selected table/columns, metadata origin,
or merge expressions may be echoed as an assertion, but it cannot unlock high
confidence.

`query_fingerprint` is the canonical query provenance field for v1. Legacy
input may contain a deprecated `query_hash` assertion, but normalized v1
examples and output should use `query_fingerprint`. `result_digest` is the
canonical payload-binding field for the full `digest_payload_v1` report-input
contract. `rowset_digest` may be accepted only as a legacy alias when it has
identical `digest_payload_v1` semantics and digest input, not as a digest over
rows alone. `limit_stage` must be one of `none`, `after_denominator`, or
`before_denominator`.
`before_denominator` invalidates complete-scope contribution evidence. The
script must validate that provenance metadata matches the report's metric,
dimensions, scope filters, current window, baseline windows, baseline method,
baseline value semantic, and any normalization factor before treating it as
trusted evidence.

If complete-scope metadata is only caller-asserted, v1 may preserve it under an
`input_assertions` or debug section, but normalized output must cap report and
mover confidence below `high` and must not emit trusted `contribution_pct`.

For period-split rows, row completeness and absence trust are separate from
contribution scope. The authoritative v1 absence/zero-fill contract is
`zero_fill_evidence` with nested `period_value_trust`. V1 must not emit
`period_absence_trust` as a canonical field; if legacy input includes it, treat
it only as a caller assertion/debug alias and do not use it for confidence or
lifecycle decisions. A missing opposite-period entity row may be interpreted
as exact zero only when trusted `zero_fill_evidence.period_value_trust`
establishes one of these conditions for the missing side:

- `period_value_trust.<side>: "complete_grouped_scope"` with
  `current_period_complete: true`, `baseline_period_complete: true`,
  `grouped_scope_complete: true`, `grouped_dimensions` matching `dimensions`,
  and no pre-group or pre-denominator source limit.
- `period_value_trust.<side>: "trusted_full_scope_join"` with
  `full_scope_joined_grouped_rowset: true`,
  `join_completed_before_output_limit: true` or
  `computed_before_output_limit: true`, `grouped_dimensions` matching
  `dimensions`, and no pre-join source limit.

Absence evidence is not required for ordinary positive/positive existing rows.
If both current and baseline support values are present and greater than zero
for the same entity key, the row can be classified as `existing` without
proving absence elsewhere in the grouped scope. Trusted absence or zero-fill
evidence is required only when a classification or flag depends on a missing
opposite-period row, a zero-valued support side, a lifecycle label such as
`new` or `disappeared`, or sparse candidate flags such as
`sparse_new_candidate` and `sparse_disappeared_candidate`.

Zero-value trust is row-shape-neutral. A numeric `current_*: 0` or
`baseline_*: 0` in a combined row proves the selected metric value for that
returned row, but it does not by itself prove lifecycle absence, zero-fill
evidence, or that the entity was absent from the opposite period. The input must
also include trusted evidence that the zero came from a complete grouped scope
or a trusted full-scope join for the same metric, dimensions, scope, windows,
and baseline method.

V1 should model this with explicit trust metadata. A zero-fill evidence object
may apply to the whole report with `applies_to: {"scope": "report"}` or to a
specific row with `applies_to: {"row_key": {...}}`. Trusted
`period_value_trust` is part of that zero-fill or absence evidence object, not a
sibling caller-editable trust input. A top-level or row-level
`period_value_trust` outside trusted evidence is only a caller assertion and
must not unlock lifecycle absence. To unlock lifecycle absence, a matching
`evidence_type: "zero_fill_evidence"` object, including its nested
`period_value_trust`, must also be present under
`trusted_context.trusted_evidence`, must match the report or row `applies_to`
selector, must carry the same `query_fingerprint` and `result_digest` as the
trusted context, and must pass the recomputed result-digest validation:

Public JSON may still produce useful movement rows when values are present.
However, it cannot claim trusted `new` or `disappeared` lifecycle solely through
self-attested fields such as `period_value_trust`,
`complete_grouped_scope`, `trusted_full_scope_join`, or
`evidence_source: "trusted_template_generator"`. Those fields are assertions
unless they arrive through skill-controlled `trusted_context`.

```json
{
  "evidence_id": "zero-fill-full-scope-join-v1",
  "evidence_type": "zero_fill_evidence",
  "applies_to": {"scope": "report"},
  "evidence_source": "trusted_template_generator",
  "generator_name": "bot-insights-attribution-sql",
  "generator_version": "1.0.0",
  "template_id": "full_scope_joined_pre_limit_v1",
  "query_fingerprint": "sha256:...",
  "result_digest": "sha256:...",
  "period_value_trust": {
    "current": "complete_grouped_scope",
    "baseline": "trusted_full_scope_join"
  },
  "metric": "requests",
  "metric_expression": "<metadata_derived_metric_expr>",
  "metric_semantics_reviewed": true,
  "selected_table": "bot_agg_path_day",
  "selected_columns": [
    "timestamp",
    "request_host",
    "request_path_norm",
    "bot_class",
    "sum(cnt_all)"
  ],
  "metadata_origin": "direct_hydrolix_table_metadata",
  "metadata_fingerprint": "sha256:...",
  "metadata_retrieval_identity": "hydrolix-mcp:get_table_info:bot_agg_path_day:<retrieved_at>",
  "merge_expressions": {
    "sum(cnt_all)": "sumMerge(`sum(cnt_all)`)"
  },
  "dimensions": ["request_path_norm", "bot_class"],
  "grouped_dimensions": ["request_path_norm", "bot_class"],
  "scope": {"request_host": "www.example.com"},
  "applied_scope_filters": {"request_host": "www.example.com"},
  "current_window": {"start": "2026-03-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
  "baseline_windows": [
    {"start": "2026-02-01T00:00:00Z", "end": "2026-03-01T00:00:00Z", "label": "previous_month"}
  ],
  "baseline_method": "single_previous_window",
  "baseline_value_semantic": "duration_normalized_to_current_window",
  "baseline_normalization": {
    "method": "scale_baseline_to_current_window_duration",
    "current_duration_seconds": 2678400,
    "baseline_duration_seconds": 2419200,
    "factor": 1.107143,
    "factor_expression": "current_duration_seconds / baseline_duration_seconds",
    "applies_to": ["baseline"]
  },
  "scope_matches_report": true,
  "windows_match_report": true,
  "baseline_method_matches_report": true,
  "grouped_scope_complete": true,
  "full_scope_joined_grouped_rowset": true,
  "computed_before_output_limit": true,
  "source_limit_applied_before_zero_fill": false,
  "limit_stage": "after_denominator"
}
```

Accepted `period_value_trust` values are `explicit_value_only`,
`complete_grouped_scope`, and `trusted_full_scope_join`. Only
`complete_grouped_scope` and `trusted_full_scope_join` can establish absence
for zero-valued period support. Combined rows produced by separately limited
top-N current and baseline queries are not enough evidence, even when missing
values were filled with zero upstream.

If input JSON includes `period_value_trust` outside trusted
`zero_fill_evidence` or outside a matching object in
`trusted_context.trusted_evidence`, the normalizer may echo it as
`period_value_trust_assertion` for debugging, but must ignore it for confidence,
zero-fill, lifecycle, and contribution decisions.

If trusted `zero_fill_evidence.period_value_trust` is missing,
`explicit_value_only`, caller-asserted, or unproven for the needed side,
absence from the other period is unknown, not zero. This is common when current
and baseline were queried as separate limited top-N result sets. In that case
the script must not classify an entity as `new` or `disappeared` based on
absence. Rows with both period values present may still be evaluated. The
default v1 behavior for rows missing one side because of untrusted absence is
to exclude them from ranked `movers`, returned-row totals, and presence
buckets, and to add a `not_evaluated_components` entry plus a
`period_absence_not_trusted` limitation. A future option such as
`--include-not-evaluated` may emit those rows with
`presence_lifecycle: "not_evaluated"` and
`support_change_label: "not_evaluated"`, no `rank`, no contribution
percentage, and no effect on totals or buckets.

Rows contain one baseline value per entity with explicit value semantics. V1
does not implement local baseline-window reduction. The input and normalized
report must include `baseline_value_semantic` so consumers know whether
`baseline` fields are raw window totals or duration-normalized comparison
values.

Accepted v1 baseline value semantics:

- `raw_total_window`: current and baseline values are exact totals over their
  declared windows. This is valid for "total March versus total February"
  comparisons, but it includes calendar-length effects. When the current and
  baseline durations differ, the report must add a limitation such as
  `calendar_length_difference_not_normalized` and must not describe the delta
  as duration-normalized traffic movement.
- `duration_normalized_to_current_window`: baseline values were scaled to the
  current window duration before delta, percentage-change, ranking, and
  contribution math. For example, a February 2026 raw baseline compared with
  March 2026 should be multiplied by `31 / 28` before being compared as a
  duration-normalized month-over-month mover.
- `externally_precomputed_baseline`: the input already contains one baseline
  value per entity from an external reviewed method. Trusted high confidence
  requires valid direct-MCP `trusted_context` plus evidence naming that method
  and its coverage.

When a trusted generator applies normalization, it must attach
`baseline_normalization` metadata with `method`, source and target window
durations, the numeric `factor`, the expression or formula used to derive the
factor, and the output fields to which the factor was applied. The normalizer
must validate that metadata against `current_window`, `baseline_windows`, and
`baseline_value_semantic` before using duration-normalized baselines for high
confidence.

The canonical normalized mover value field is `baseline`. Input aliases such
as `baseline_requests`, `baseline_raw_requests`,
`normalized_baseline_requests`, or `<metric>_baseline` must be mapped to the
normalized output field according to `metric` and
`baseline_value_semantic`. `baseline_normalization.applies_to` in normalized
metadata and trusted evidence must name normalized output fields, so v1 uses
`["baseline"]`. Input-side metadata that says
`applies_to: ["baseline_requests"]` may be accepted as a legacy assertion only
after that alias maps unambiguously to output `baseline`; normalized output and
trusted evidence must rewrite it to `["baseline"]`. Raw support fields such as
`baseline_support_raw` and raw value fields such as `baseline_raw_requests` are
not normalized baseline output fields and must not appear in
`baseline_normalization.applies_to`.

If multiple `baseline_windows` are supplied, the caller or trusted generator
must pre-reduce them into one explicitly semantic baseline value per entity
before rows reach `attribution.py`, and must provide `baseline_method` to
explain the reduction, such as `mean_of_baseline_windows`,
`duration_weighted_mean_of_baseline_windows`, or
`externally_precomputed_baseline`. Period-split input that contains separate
baseline rows for multiple baseline windows, for example with
`baseline_window_label` or multiple `period: "baseline"` rows for the same
entity/window family, must be rejected or marked not evaluated unless it also
contains a single explicitly normalized baseline value for that entity and the
duplicate window rows are ignored. The normalizer must add
`baseline_windows_not_reduced` when it rejects or skips unreduced baseline
window rows.

Presence lifecycle and support-change classification are
count/support/period-presence based. They must not be inferred from arbitrary
selected metric values. The script should derive
`current_support_raw` and `baseline_support_raw` in this order:

- For reviewed additive count metrics that represent entity volume, such as
  requests or event counts, the selected current metric value and the raw
  baseline metric value may be used as raw support. If the displayed baseline
  metric is duration-normalized, support thresholds still use the raw observed
  baseline count.
- For rates, averages, percentiles, approximate uniques, scores, ratios, or
  other non-volume metrics, presence lifecycle and support-change labels require
  separate raw support fields such as `current_support_raw` and
  `baseline_support_raw`, or metric-specific reviewed aliases documented in
  metadata. Legacy aliases such as `current_support_count` and
  `baseline_raw_support_count` may be mapped to the raw support fields.
- If a non-volume metric lacks support fields, movement values may still be
  ranked when both current and baseline metric values are present, but
  `presence_lifecycle` and `support_change_label` must be `not_evaluated` for
  those emitted rows, or omitted if the selected output mode suppresses
  lifecycle labels. The report must add
  `lifecycle_support_missing` to `not_evaluated_components` and `limitations`.

Movement math is independent from lifecycle/support classification. The
normalizer may compute `absolute_delta`, `pct_change`, `direction`, and rank
for any selected metric whose current and baseline values are present or safely
zero-filled. Lifecycle and support-change labels require separate support
evidence and may be `not_evaluated` even when the row is a valid ranked mover.
`min_count` thresholds, sparse lifecycle flags, and high-confidence lifecycle
support checks must use raw observed support: `current_support_raw` and
`baseline_support_raw`. Optional display fields such as
`baseline_support_normalized` may be emitted only for comparison or
explanation. They must not be used for lifecycle thresholds. Duration-normalized
baseline metric values may still be used for movement delta, ranking,
percentage change, and contribution when the selected metric's baseline value
semantic is duration-normalized.

Exact trusted absence is different from sparse positive support. Normalized
multi-window baselines may be fractional, for example when several baseline
windows are averaged or duration-weighted. A value where `0 <
baseline_support_normalized < 1` is display-only sparse support, not zero,
unless trusted evidence separately establishes absence for that entity and
period. For lifecycle thresholds, use `baseline_support_raw`; `0 <
baseline_support_raw < min_count` is sparse existing support, not absence.
The same rule applies to sparse current support.

The v1 default support threshold is `min_count: 100`, matching the existing
script behavior. `attribution.py` should expose a `--min-count` CLI override.
Metric-specific default thresholds are deferred to v2 or later registry work.

Accepted `contribution_basis` values:

- `complete_rowset`: all grouped rows were supplied and the script may compute
  `total_abs_delta` and `contribution_pct` locally only when rowset
  completeness is backed by trusted evidence. A caller-supplied
  `rowset_complete: true` without trusted provenance is an assertion and cannot
  unlock high confidence or trusted contribution.
- `complete_scope_pre_limit`: Hydrolix computed both
  `complete_scope_total_abs_delta` and per-row `contribution_pct` over the full
  grouped scope before applying an output limit, and direct-MCP wrapper
  evidence proves that denominator stage.
- `provided_complete_scope`: caller supplied complete-scope contribution values
  with explicit metadata. These values are trusted only when the metadata is
  generated by a reviewed template path and delivered through the direct-MCP
  wrapper, a matching typed list entry with `evidence_type:
  "provided_contribution_evidence"` is present in
  `trusted_context.trusted_evidence`, the result digest and `applies_to` selector
  match, and validation passes; otherwise they remain caller assertions.
- `none`: contribution is not safe to compute or emit.

In normalized `bot_attribution_report.v1`, `movers[].contribution_pct` is
reserved for validated trusted contribution only. Caller-supplied,
metadata-poor, or otherwise untrusted contribution percentages may be echoed
only in `input_assertions`, `debug`, or raw input echo sections. They must not
appear in normalized mover rows.

Invalid or caller-asserted contribution evidence must degrade the normalized
report to `contribution_basis: "none"` and withhold
`movers[].contribution_pct`. This applies even when the input contains
complete-scope-looking denominators, per-row percentages, `rowset_complete:
true`, or `evidence_source: "trusted_template_generator"` outside
skill-controlled `trusted_context`.

`complete_rowset` is compute-safe only when trusted evidence establishes that
the local rows contain every grouped row in the declared report scope before
any result limiting, and that evidence is supplied through
`trusted_context.trusted_evidence` as a typed list entry with
`evidence_type: "complete_rowset_evidence"`, a matching `applies_to` selector,
and a matching recomputed `result_digest`.
Recommended typed trusted evidence list entry:

```json
{
  "evidence_id": "complete-rowset-single-dimension-v1",
    "evidence_type": "complete_rowset_evidence",
    "applies_to": {"scope": "report"},
    "evidence_source": "trusted_template_generator",
    "generator_name": "bot-insights-attribution-sql",
    "generator_version": "1.0.0",
    "template_id": "complete_rowset_single_dimension_v1",
    "query_fingerprint": "sha256:...",
    "result_digest": "sha256:...",
    "source_path": "bundled:skills/bot-insights/references/attribution-sql.md#complete-rowset-v1",
    "metric": "requests",
    "metric_expression": "<metadata_derived_metric_expr>",
    "metric_semantics_reviewed": true,
    "selected_table": "bot_summary_day",
    "selected_columns": ["timestamp", "client_asn", "request_host", "sum(cnt_all)"],
    "metadata_origin": "direct_hydrolix_table_metadata",
    "metadata_fingerprint": "sha256:...",
    "metadata_retrieval_identity": "hydrolix-mcp:get_table_info:bot_summary_day:<retrieved_at>",
    "merge_expressions": {
      "sum(cnt_all)": "sumMerge(`sum(cnt_all)`)"
    },
    "dimensions": ["client_asn"],
    "grouped_dimensions": ["client_asn"],
    "scope": {"request_host": "www.example.com"},
    "applied_scope_filters": {"request_host": "www.example.com"},
    "current_window": {"start": "2026-03-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
    "baseline_windows": [
      {"start": "2026-02-01T00:00:00Z", "end": "2026-03-01T00:00:00Z", "label": "previous_month"}
    ],
    "baseline_method": "single_previous_window",
    "baseline_value_semantic": "duration_normalized_to_current_window",
    "baseline_normalization": {
      "method": "scale_baseline_to_current_window_duration",
      "current_duration_seconds": 2678400,
      "baseline_duration_seconds": 2419200,
      "factor": 1.107143,
      "factor_expression": "current_duration_seconds / baseline_duration_seconds",
      "applies_to": ["baseline"]
    },
    "scope_matches_report": true,
    "windows_match_report": true,
    "baseline_method_matches_report": true,
    "grouped_scope_complete": true,
    "all_grouped_rows_returned": true,
    "source_limit_applied_before_grouping": false,
    "source_limit_applied_before_denominator": false,
    "pre_group_filter_applied": false,
    "pre_denominator_filter_applied": false,
    "pre_group_or_denominator_filter_outside_declared_scope": false,
    "limit_stage": "none"
}
```

The difference from `complete_scope_pre_limit` is where contribution is
computed. With `complete_rowset`, every grouped row is present in local input,
so `attribution.py` may compute `total_abs_delta` and missing
`contribution_pct` locally after validating additive metric semantics. With
`complete_scope_pre_limit`, local input may be a top-N subset; Hydrolix must
have computed `complete_scope_total_abs_delta` and per-row `contribution_pct`
over the complete grouped scope before the final output limit. The local script
may preserve and validate those percentages, but it must not reconstruct
missing percentages from returned rows.

Scorecard handoff must separate two semantics:

- Provided contribution may be trusted and preserved when the attribution report
  has trusted evidence for `complete_rowset`, `complete_scope_pre_limit`, or
  `provided_complete_scope` from a matching typed
  `trusted_context.trusted_evidence` entry, the evidence is bound to the
  recomputed result digest, the `applies_to` selector matches the report or row,
  the supplied percentage passes algebraic consistency validation, and the row
  already contains `contribution_pct`.
- Missing contribution may be locally computed only for additive metrics when
  `contribution_basis: "complete_rowset"` and trusted rowset-completeness
  evidence establish that all grouped rows for the report scope are present.

`complete_scope_pre_limit` and `provided_complete_scope` are preserve-safe, not
compute-safe. They must not cause `scorecard.py` to reconstruct missing
percentages from the returned rows. Until `scorecard.py` has separate
preserve-safe and compute-safe gates, scorecard exports for these bases must
include trusted `contribution_pct` on every exported row, omit legacy
compute-safe metadata such as `contribution_basis: "complete_scope"` unless the
scorecard script has been updated, and use advanced evidence metadata names that
older `scorecard.py` ignores. If that cannot be guaranteed, scorecard export
must be disabled for those bases.

`complete_scope_pre_limit` is only valid when direct-MCP wrapper evidence
proves all of these:

- the evidence object was supplied as a `trusted_context.trusted_evidence` typed
  list entry with `evidence_type:
  "complete_scope_pre_limit_evidence"`, a matching `applies_to` selector, and a
  `result_digest` that matches the recomputed `digest_payload_v1` digest;
- top-level `complete_scope_total_abs_delta`, computed before output limiting,
  or an identical non-null per-row `complete_scope_total_abs_delta` that the
  script can promote to top-level metadata;
- per-row `contribution_pct`, computed from that same complete-scope
  denominator before output limiting;
- `computed_before_output_limit: true`;
- evidence that the denominator was computed over the same metric, dimensions,
  scope, current window, baseline windows, and baseline method as the report;
- evidence that no source limit or pre-denominator filter outside the declared
  report scope was applied before denominator computation.

Recommended typed trusted evidence list entry:

```json
{
  "evidence_id": "complete-scope-single-dimension-v1",
    "evidence_type": "complete_scope_pre_limit_evidence",
    "applies_to": {"scope": "report"},
    "evidence_source": "trusted_template_generator",
    "generator_name": "bot-insights-attribution-sql",
    "generator_version": "1.0.0",
    "template_id": "complete_scope_single_dimension_v1",
    "query_fingerprint": "sha256:...",
    "result_digest": "sha256:...",
    "metric": "requests",
    "metric_expression": "<metadata_derived_metric_expr>",
    "metric_semantics_reviewed": true,
    "selected_table": "bot_summary_day",
    "selected_columns": ["timestamp", "client_asn", "request_host", "sum(cnt_all)"],
    "metadata_origin": "direct_hydrolix_table_metadata",
    "metadata_fingerprint": "sha256:...",
    "metadata_retrieval_identity": "hydrolix-mcp:get_table_info:bot_summary_day:<retrieved_at>",
    "merge_expressions": {
      "sum(cnt_all)": "sumMerge(`sum(cnt_all)`)"
    },
    "dimensions": ["client_asn"],
    "grouped_dimensions": ["client_asn"],
    "scope": {"request_host": "www.example.com"},
    "applied_scope_filters": {"request_host": "www.example.com"},
    "current_window": {"start": "2026-03-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
    "baseline_windows": [
      {"start": "2026-02-01T00:00:00Z", "end": "2026-03-01T00:00:00Z", "label": "previous_month"}
    ],
    "baseline_method": "single_previous_window",
    "baseline_value_semantic": "duration_normalized_to_current_window",
    "baseline_normalization": {
      "method": "scale_baseline_to_current_window_duration",
      "current_duration_seconds": 2678400,
      "baseline_duration_seconds": 2419200,
      "factor": 1.107143,
      "factor_expression": "current_duration_seconds / baseline_duration_seconds",
      "applies_to": ["baseline"]
    },
    "scope_matches_report": true,
    "windows_match_report": true,
    "baseline_method_matches_report": true,
    "denominator_expression": "sum(abs(current_requests - baseline_requests)) over ()",
    "denominator_basis": "sum_abs_delta",
    "computed_over_complete_grouped_scope": true,
    "computed_before_output_limit": true,
    "source_limit_applied_before_denominator": false,
    "pre_denominator_filter_applied": false,
    "limit_stage": "after_denominator"
}
```

For MCP `columns`/`rows` output, the denominator often appears as a repeated
column on each returned row. The script may promote that repeated value to
top-level metadata only when every returned row has the same numeric denominator
and any supplied top-level denominator matches it. Missing denominators,
mixed denominators, non-numeric denominators, or a mismatch between top-level
and per-row denominators invalidate the complete-scope evidence. Identical
per-row denominators prove only consistency among returned rows; they do not by
themselves prove full-scope coverage. The report should then withhold or
discard `contribution_pct`, set contribution basis to `none` in the normalized
output, and add `complete_scope_denominator_invalid` or
`complete_scope_not_proven` to `not_evaluated_components` and `limitations`.

The local script must not reconstruct `contribution_pct` for
`complete_scope_pre_limit` rows from returned rows, even when
`complete_scope_total_abs_delta` is present.

Provided per-row percentages must still be algebraically validated whenever a
complete-scope denominator is present. For each row:

```text
abs(provided_contribution_pct - abs(absolute_delta) / complete_scope_total_abs_delta * 100) <= contribution_pct_tolerance_pp
```

The default `contribution_pct_tolerance_pp` should be `0.01` percentage points
and may be made configurable. If `complete_scope_total_abs_delta` is zero,
`contribution_pct` must be omitted or `null` unless a later schema explicitly
defines zero-denominator behavior. If a supplied value fails consistency
validation, the script must discard it from normalized output and add
`provided_contribution_inconsistent` to `not_evaluated_components` and
`limitations`. This validation does not permit reconstruction of missing
percentages for preserve-only bases; missing `contribution_pct` remains missing.

Numeric hygiene is fail-closed for core fields:

- Reject non-finite JSON numbers and in-process numeric values: `NaN`,
  `Infinity`, and `-Infinity` are invalid even if the host parser accepts them.
- Support counts such as `current_support_raw` and `baseline_support_raw` must
  be non-negative integers. Negative support is invalid input for lifecycle and
  support-change classification.
- Denominators used for contribution must be non-negative finite decimals.
  `complete_scope_total_abs_delta < 0` is invalid. A zero denominator is valid
  only as the explicit "no absolute movement" case and requires null or
  omitted `contribution_pct`.
- Normalized metric values and deltas may be negative only when the reviewed
  metric semantics allow negative values. Entity-volume count metrics must be
  non-negative before delta calculation.
- `contribution_pct` must be `null`, omitted, or a finite decimal in
  `[0, 100]` after rounding to two decimals. Values outside that range are
  invalid even if the algebraic formula could produce them because of bad
  input signs.
- Rounding happens before contribution consistency checks and digest
  calculation using the precision rules from `digest_payload_v1`. The same
  digest-canonical rounded values must be used for consistency validation and
  digest construction. Emitted public output may use shorter display precision
  for the same values.

`provided_complete_scope` requires an explicit trusted evidence object to be
trusted. If the same shape arrives with `evidence_source: "caller_assertion"` or
`manual_paste`, treat it as `provided_contribution_assertion` for debug only:

```json
{
  "evidence_id": "provided-contribution-single-dimension-v1",
  "evidence_type": "provided_contribution_evidence",
  "applies_to": {"scope": "report"},
  "evidence_source": "trusted_template_generator",
  "generator_name": "bot-insights-attribution-sql",
  "generator_version": "1.0.0",
  "template_id": "complete_scope_provided_contribution_v1",
  "query_fingerprint": "sha256:...",
  "result_digest": "sha256:...",
  "metric": "requests",
  "metric_expression": "<metadata_derived_metric_expr>",
  "reviewed_metric_kind": "additive_count",
  "metric_semantics_reviewed": true,
  "selected_table": "bot_summary_day",
  "selected_columns": ["timestamp", "client_asn", "request_host", "sum(cnt_all)"],
  "metadata_origin": "direct_hydrolix_table_metadata",
  "metadata_fingerprint": "sha256:...",
  "metadata_retrieval_identity": "hydrolix-mcp:get_table_info:bot_summary_day:<retrieved_at>",
  "merge_expressions": {
    "sum(cnt_all)": "sumMerge(`sum(cnt_all)`)"
  },
  "dimensions": ["client_asn"],
  "grouped_dimensions": ["client_asn"],
  "scope": {"request_host": "www.example.com"},
  "applied_scope_filters": {"request_host": "www.example.com"},
  "current_window": {"start": "2026-03-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
  "baseline_windows": [
    {"start": "2026-02-01T00:00:00Z", "end": "2026-03-01T00:00:00Z", "label": "previous_month"}
  ],
  "baseline_method": "single_previous_window",
  "baseline_value_semantic": "duration_normalized_to_current_window",
  "baseline_normalization": {
    "method": "scale_baseline_to_current_window_duration",
    "current_duration_seconds": 2678400,
    "baseline_duration_seconds": 2419200,
    "factor": 1.107143,
    "factor_expression": "current_duration_seconds / baseline_duration_seconds",
    "applies_to": ["baseline"]
  },
  "scope_matches_report": true,
  "windows_match_report": true,
  "baseline_method_matches_report": true,
  "contribution_pct_field": "contribution_pct",
  "denominator_field": "complete_scope_total_abs_delta",
  "denominator_expression": "sum(abs(current_requests - baseline_requests)) over ()",
  "denominator_basis": "sum_abs_delta",
  "denominator_scope_matches_report": true,
  "computed_over_complete_grouped_scope": true,
  "computed_before_output_limit": true,
  "source_limit_applied_before_denominator": false,
  "pre_denominator_filter_applied": false,
  "per_row_contribution": true,
  "limit_stage": "after_denominator",
  "contribution_pct_tolerance_pp": 0.01
}
```

The evidence must establish reviewed additive metric semantics from the v1
allowlist, direct-MCP wrapper provenance, a per-row `contribution_pct`,
denominator identity across rows or top-level metadata, algebraic consistency
within tolerance, no pre-denominator source limit or filter outside the declared
report scope, and a complete-scope denominator for the same metric, dimensions,
scope, windows, baseline method, baseline value semantic, and any baseline
normalization metadata as the report. If any of those fields are missing, false,
caller-asserted, or inconsistent, the script may preserve the raw input row for
debugging, but the normalized report must not emit trusted `contribution_pct`.
The normal degraded output should set
`contribution_basis: "none"`, discard any untrusted per-row
`contribution_pct`, and record the evidence failure in
`not_evaluated_components` and `limitations`.

V1 accepts non-additive metrics as movers, but additive contribution is allowed
only for a hardcoded reviewed metric allowlist and alias map. Initial additive
entity-volume aliases may include
`requests`/`cnt_all`, `cnt_2xx`, `cnt_4xx`, `cnt_429`, `cnt_5xx`,
`cnt_cached`, `cnt_cache_miss`, `cnt_blocked`, `cnt_auth_fail`, and
`cnt_biz_fail`. The allowlist must define the canonical metric, accepted
aliases, selected current/baseline field aliases, and whether the metric can
serve as lifecycle support.

Local duplicate aggregation is fail-closed. Additive metric semantics alone do
not prove that duplicate rows for the same entity/period are safe to sum,
because duplicates may represent repeated query output, overlapping source
partitions, mixed fixtures, or accidental concatenation. By default, duplicate
entity/period keys are fatal for the affected row shape. Aggregation is allowed
only when trusted `duplicate_aggregation_evidence` is present under
`trusted_context.trusted_evidence` as a matching typed list entry, bound to the
same `query_fingerprint` and `result_digest`, matching the relevant
`applies_to` selector, and explicitly naming the partitioning or source grouping
semantics that make summation valid.

Recommended typed trusted duplicate aggregation evidence list entry:

```json
{
  "evidence_id": "duplicate-aggregation-partitioned-period-rows-v1",
    "evidence_type": "duplicate_aggregation_evidence",
    "applies_to": {"scope": "report"},
    "evidence_source": "trusted_template_generator",
    "generator_name": "bot-insights-attribution-sql",
    "generator_version": "1.0.0",
    "template_id": "partitioned_period_rows_v1",
    "query_fingerprint": "sha256:...",
    "result_digest": "sha256:...",
    "metric": "requests",
    "metric_expression": "<metadata_derived_metric_expr>",
    "metric_semantics_reviewed": true,
    "selected_table": "bot_summary_day",
    "selected_columns": ["timestamp", "client_asn", "request_host", "hdx_cdn", "sum(cnt_all)"],
    "metadata_origin": "direct_hydrolix_table_metadata",
    "metadata_fingerprint": "sha256:...",
    "metadata_retrieval_identity": "hydrolix-mcp:get_table_info:bot_summary_day:<retrieved_at>",
    "merge_expressions": {
      "sum(cnt_all)": "sumMerge(`sum(cnt_all)`)"
    },
    "dimensions": ["client_asn"],
    "grouped_dimensions": ["client_asn"],
    "scope": {"request_host": "www.example.com"},
    "applied_scope_filters": {"request_host": "www.example.com"},
    "current_window": {"start": "2026-03-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
    "baseline_windows": [
      {"start": "2026-02-01T00:00:00Z", "end": "2026-03-01T00:00:00Z", "label": "previous_month"}
    ],
    "baseline_method": "single_previous_window",
    "baseline_value_semantic": "duration_normalized_to_current_window",
    "baseline_normalization": {
      "method": "scale_baseline_to_current_window_duration",
      "current_duration_seconds": 2678400,
      "baseline_duration_seconds": 2419200,
      "factor": 1.107143,
      "factor_expression": "current_duration_seconds / baseline_duration_seconds",
      "applies_to": ["baseline"]
    },
    "scope_matches_report": true,
    "windows_match_report": true,
    "baseline_method_matches_report": true,
    "period_field": "period",
    "duplicate_key_fields": ["period", "client_asn"],
    "partition_fields": ["hdx_cdn"],
    "partition_semantics": "disjoint_source_partitions",
    "aggregation_allowed": true,
    "aggregation_functions": {
      "current": "sum",
      "baseline": "sum",
      "current_support_raw": "sum",
      "baseline_support_raw": "sum"
    }
}
```

Rates, averages, percentiles, approximate uniques, ratios, scores, and unique
counts are non-additive in v1 unless the allowlist explicitly names a reviewed
aggregation semantic for that exact metric. They may still be compared as
movers when both values are present, but `contribution_pct` must be withheld and
duplicate aggregation must be rejected unless trusted duplicate-aggregation
evidence names reviewed semantics for that exact metric. A caller cannot enable
contribution math by labeling such a metric as `metric_kind:
"additive_count"`. Their presence lifecycle and support-change labels also
require independent support counts; a high error rate with no request support
count, for example, can be ranked as a metric mover but cannot prove `new`,
`disappeared`, `existing`, or sparse lifecycle status.

### Combined Rows

Combined rows contain current and baseline values in the same row:

```json
{
  "request_path_norm": "/api/search",
  "bot_class": "good",
  "current_requests": 64000,
  "baseline_requests": 10000
}
```

Metric aliases are selected-metric aliases, not request-only aliases. For the
configured `metric`, accept exact current/baseline variants such as:

- `current` / `baseline`
- `current_<metric>` / `baseline_<metric>`
- `<metric>_current` / `<metric>_baseline`
- examples: `current_cnt_429` / `baseline_cnt_429`,
  `cnt_429_current` / `cnt_429_baseline`,
  `current_cnt_cache_miss` / `baseline_cnt_cache_miss`,
  `cnt_cache_miss_current` / `cnt_cache_miss_baseline`

If the selected metric is a semantic alias such as `requests`, the normalizer
may map it to a reviewed metric column such as `cnt_all`. It must not select an
unrelated current/baseline pair just because the field names look familiar.

Combined-row zeroes follow the same skill-controlled boundary as period-split
absence.
`current_<metric>: 0` or `baseline_<metric>: 0` is not trusted lifecycle
absence evidence unless nested `period_value_trust` inside trusted
`zero_fill_evidence` establishes that side's zero over the complete grouped
report scope or a trusted full-scope join. Without that evidence, the row may
be used for metric delta if both values are present, but it must not assert
`new`, `disappeared`, sparse presence lifecycle candidate flags, or zero-fill
evidence.

### Period-Split Rows

Period-split rows contain one row per entity and period:

```json
{"period": "current", "client_asn": "12345", "requests": 64000}
{"period": "baseline", "client_asn": "12345", "requests": 10000}
```

The script should normalize `after` to `current` and `before` to `baseline`
when appropriate.

Period-split normalization must first check uniqueness by the requested
dimensions and period. Duplicate rows for the same dimension key and period
are rejected by default, even for additive metrics. The normalizer may locally
aggregate duplicates only when trusted `duplicate_aggregation_evidence`
explicitly proves disjoint partitioning or source grouping semantics for the
same metric, dimensions, scope, windows, selected table, metadata fingerprint,
and result digest. Caller assertions are not enough. This grouping is not a
baseline-window reducer. When `baseline_windows` contains more than one
window, the period-split input must already expose one normalized
`baseline` period value per entity; rows split by individual baseline windows
must be rejected or skipped with `baseline_windows_not_reduced`. After
entity/period grouping, missing
opposite-period rows are zero-fillable only under trusted
`zero_fill_evidence.period_value_trust` evidence rules above. Separate limited
top-N current and baseline queries are not enough evidence; absence from one
result may simply mean "outside that top-N."
Unsafe absence must be reported with `not_evaluated_components` instead of
becoming `new`, `disappeared`, or a zero-valued delta. In v1, those unsafe
one-sided rows are excluded from ranked `movers`, totals, and buckets by
default.

Recommended unsafe-absence report entry:

```json
{
  "name": "presence_lifecycle",
  "reason": "period_absence_not_trusted",
  "skipped_count": 37,
  "sample_entity_values": [
    {"client_asn": "12345"},
    {"client_asn": "67890"}
  ],
  "required_metadata": [
    "trusted zero_fill_evidence.period_value_trust.<side>: complete_grouped_scope",
    "or trusted zero_fill_evidence.period_value_trust.<side>: trusted_full_scope_join"
  ]
}
```

`not_evaluated_components` must be bounded. For skipped unsafe rows, emit a
numeric `skipped_count` and at most `sample_entity_values_limit` sample entity
keys, with a v1 default limit of 10. Do not dump every skipped entity into the
report. Samples are diagnostic only and must not affect ranking, totals,
buckets, or digest identity unless explicitly included in the canonical mapped
rows.

### MCP Result JSON

MCP-style `columns` and `rows` input should be accepted, but plain MCP output
does not contain enough metadata to prove complete-scope contribution or
lifecycle absence. The preferred MCP input for standalone use is a wrapper with
a metadata sidecar, but that sidecar is still caller-editable JSON and is not
high-confidence-unlocking by itself. The v1b high-confidence MCP path is not
"JSON with better metadata"; it is a reviewed wrapper receiving the direct MCP
tool result object in memory, mapping it, computing `result_digest`, attaching
trusted evidence through `trusted_context`, and invoking
`normalize_attribution(input_doc, trusted_context=trusted_context)`
in-process. If the result is saved, pasted, round-tripped through a notebook,
or passed as ordinary CLI/stdin JSON, the normalizer must treat it as
untrusted MCP JSON, behave as
`normalize_attribution(input_doc, trusted_context=None)`, and cap confidence
below high.

The nested evidence object in this example is intentionally abbreviated to show
the wrapper shape; by itself it is not high-confidence-unlocking. A real
high-confidence wrapper must use the complete evidence fields from the
canonical example above and must arrive through the direct-MCP v1
skill-controlled boundary:

```json
{
  "schema_version": "bot_attribution_input.v1",
  "metadata": {
    "metric": "requests",
    "dimensions": ["client_asn"],
    "scope": {"request_host": "www.example.com"},
    "current_window": {"start": "2026-03-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
    "baseline_windows": [
      {"start": "2026-02-01T00:00:00Z", "end": "2026-03-01T00:00:00Z", "label": "previous_month"}
    ],
    "baseline_method": "single_previous_window",
    "evidence_source": "trusted_template_generator",
    "generator_name": "bot-insights-attribution-sql",
    "generator_version": "1.0.0",
    "template_id": "complete_scope_single_dimension_v1",
    "query_fingerprint": "sha256:...",
    "result_digest": "sha256:...",
    "limit_stage": "after_denominator",
    "evidence_assertions": [
      {
        "evidence_id": "complete-scope-single-dimension-v1",
        "evidence_type": "complete_scope_pre_limit_evidence",
        "applies_to": {"scope": "report"},
        "evidence_source": "trusted_template_generator",
      "metric": "requests",
      "grouped_dimensions": ["client_asn"],
      "denominator_expression": "sum(abs(current_requests - baseline_requests)) over ()",
      "computed_over_complete_grouped_scope": true,
      "computed_before_output_limit": true,
        "source_limit_applied_before_denominator": false
      }
    ]
  },
  "mcp_result": {
    "columns": [
      {"name": "client_asn"},
      {"name": "current_requests"},
      {"name": "baseline_requests"},
      {"name": "complete_scope_total_abs_delta"},
      {"name": "contribution_pct"}
    ],
    "rows": [
      ["12345", 64000, 10000, 160000, 33.75]
    ]
  }
}
```

Plain MCP `columns` and `rows` input should still be accepted as metadata-poor
input:

```json
{
  "columns": [
    {"name": "client_asn"},
    {"name": "current_requests"},
    {"name": "baseline_requests"}
  ],
  "rows": [
    ["12345", 64000, 10000]
  ]
}
```

Metadata-poor MCP input must be normalized conservatively. It may produce mover
deltas when dimensions and metric fields can be mapped, but it must add
`metadata_poor_input` to `limitations`, keep confidence `low`, and withhold
trusted `contribution_pct` and trusted lifecycle absence unless additional
trusted metadata is supplied. Repeated denominators in plain MCP rows prove only
row-level consistency; they do not prove full-scope coverage.

Metric inference for public MCP input is intentionally narrower than dimension
inference. The selected `metric` should be supplied by top-level input,
metadata sidecar, or CLI `--metric`. If it is missing, the normalizer may only
infer it from one unambiguous reviewed metric alias pair, such as
`current_requests` plus `baseline_requests`, after excluding support,
denominator, derived, period, and provenance columns. If more than one reviewed
metric alias pair is present, or only a non-reviewed alias is present, fail
with `ambiguous_metric_input` or `metric_input_missing` instead of guessing.

Dimension inference for public MCP input is allowed, but it must be
deterministic and confidence-capped:

- Explicit top-level `dimensions` wins after conflict validation.
- CLI `--dimensions` may fill missing dimensions for standalone runs.
- Otherwise infer dimensions from scalar row columns after excluding known
  selected-metric aliases, support fields, denominator fields, derived fields,
  period/time/window fields, and metadata/provenance fields. Exclusions include
  fields such as `current`, `baseline`, `current_*`, `baseline_*`,
  `*_current`, `*_baseline`, `absolute_delta`, `abs_delta`, `pct_change`,
  `contribution_pct`, `complete_scope_total_abs_delta`,
  `current_support_raw`, `baseline_support_raw`, `period`, `timestamp`,
  `window_start`, `window_end`, `evidence_source`, `generator_name`,
  `template_id`, `query_fingerprint`, and `result_digest`.
- Preserve MCP column order for inferred dimensions.
- Echo inferred dimensions in the normalized output and add
  `dimensions_inferred` to `confidence_reasons` or `limitations`.
- Metadata-poor inferred input remains capped below `high` even when the row
  values are internally consistent.
- If no dimensions can be inferred, or if metric and dimension inference are
  ambiguous, fail with a structured `bot_attribution_error.v1` invalid-input
  error instead of guessing.

### Row Shape Rules

Use one row shape per run.

Fatal invalid input should fail clearly because the script cannot build a
coherent aggregate comparison. Fatal cases include:

- malformed JSON;
- mixing period-split rows and combined rows;
- missing all current/baseline metric fields;
- omitting one or more requested dimensions when they cannot be supplied by CLI
  or inferred deterministically from MCP columns;
- no usable selected metric values;
- duplicate entity/period keys unless trusted duplicate-aggregation evidence
  proves valid aggregation semantics;
- duplicate or blank MCP column names;
- MCP row-length mismatches;
- MCP-style rows that cannot be mapped to columns.

Fatal output/error contract:

- CLI fatal input errors exit nonzero, preferably exit code `2` for invalid
  input, and write machine-readable JSON to stdout or stderr according to the
  CLI's normal error-output convention. The process must not emit a partial
  `bot_attribution_report.v1` for these cases.
- Library callers receive the same structured error object or a typed exception
  carrying that object. They must not receive a successful report object for
  fatal invalid input.
- The error schema is `bot_attribution_error.v1`:

```json
{
  "schema_version": "bot_attribution_error.v1",
  "error_type": "invalid_input",
  "fatal": true,
  "errors": [
    {
      "code": "mcp_row_length_mismatch",
      "message": "MCP row 3 has 4 values but 5 columns were declared.",
      "path": "$.rows[3]"
    }
  ],
  "limitations": []
}
```

Fatal error codes should be specific and deterministic. V1 should include at
least `malformed_json`, `mixed_row_shapes`, `missing_requested_dimension`,
`metric_input_missing`, `ambiguous_metric_input`,
`no_usable_metric_values`, `duplicate_entity_period_key`,
`duplicate_entity_period_key_without_trusted_aggregation`,
`duplicate_mcp_column`, `blank_mcp_column`, `mcp_row_length_mismatch`, and
`unmappable_mcp_row`, `no_inferable_dimensions`, and
`dimension_inference_ambiguous`.

Unsafe optional calculations are not fatal by default. Classifying
period-split missing rows as zero without trusted
`zero_fill_evidence.period_value_trust`, classifying combined-row numeric
zeroes as lifecycle absence without
trusted `zero_fill_evidence.period_value_trust`, rows limited before
denominator computation while
claiming complete contribution scope, caller-asserted complete-scope metadata,
invalid `complete_scope_pre_limit_evidence`, invalid
`provided_contribution_evidence`, missing per-row contribution, denominator
mismatch, contribution inconsistency, or unsupported non-additive contribution
semantics should degrade to a partial report unless a future strict mode
explicitly requests failure. The normalized output must set
  `contribution_basis: "none"` for unsafe contribution, withhold or discard
  `contribution_pct`, exclude unsafe one-sided lifecycle rows from ranked
  movers, totals, and buckets by default, except for explicitly emitted
  one-sided sparse candidate rows, and use
  `presence_lifecycle: "not_evaluated"` plus
  `support_change_label: "not_evaluated"` only for emitted rows whose selected
  metric values are present but lifecycle/support cannot be evaluated. Add
  specific
`not_evaluated_components` and `limitations` such as
`complete_scope_denominator_invalid`, `complete_scope_not_proven`,
`caller_asserted_complete_scope_not_trusted`,
`provided_contribution_inconsistent`, or
`non_additive_metric_contribution_withheld`.

Missing high-confidence metadata, raw fallback coverage caveats, lifecycle
support gaps, and untrusted period absence should likewise emit a partial
report with explicit `not_evaluated_components` unless the row cannot be
normalized at all. A future optional strict mode may promote selected optional
evidence failures to fatal errors, but permissive partial output is the v1
default.

## Output Schema

`bot_attribution_report.v1` should stay simple. One report represents one
metric and one dimension set.

```json
{
  "schema_version": "bot_attribution_report.v1",
  "method": "aggregate_delta_attribution",
  "comparison_type": "month_over_month",
  "granularity": "day",
  "table_used": "bot_summary_day",
  "summary_table_used": true,
  "scope": {"request_host": "www.example.com"},
  "current_window": {"start": "2026-03-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
  "baseline_windows": [
    {"start": "2026-02-01T00:00:00Z", "end": "2026-03-01T00:00:00Z", "label": "previous_month"}
  ],
  "baseline_method": "single_previous_window",
  "baseline_value_semantic": "duration_normalized_to_current_window",
  "baseline_normalization": {
    "method": "scale_baseline_to_current_window_duration",
    "current_duration_seconds": 2678400,
    "baseline_duration_seconds": 2419200,
    "factor": 1.107143,
    "factor_expression": "current_duration_seconds / baseline_duration_seconds",
    "applies_to": ["baseline"]
  },
  "metric": "requests",
  "metric_kind": "additive_count",
  "dimensions": ["client_asn"],
  "rowset_complete": false,
  "source_limit_applied": false,
  "output_limit_applied": true,
  "output_limit": 50,
  "totals_basis": "returned_rows",
  "total_current": 64000,
  "total_baseline": 11071.43,
  "total_delta": 52928.57,
  "total_abs_delta": 52928.57,
  "complete_scope_total_abs_delta": 160000,
  "contribution_basis": "complete_scope_pre_limit",
  "audit_evidence": [
    {
      "evidence_id": "complete-scope-single-dimension-v1",
      "evidence_type": "complete_scope_pre_limit_evidence",
      "applies_to": {"scope": "report"},
      "evidence_source": "trusted_template_generator",
    "generator_name": "bot-insights-attribution-sql",
    "generator_version": "1.0.0",
    "template_id": "complete_scope_single_dimension_v1",
    "query_fingerprint": "sha256:...",
    "result_digest": "sha256:...",
    "metric": "requests",
    "denominator_expression": "sum(abs(current_requests - baseline_requests)) over ()",
    "denominator_basis": "sum_abs_delta",
    "metric_expression": "<metadata_derived_metric_expr>",
    "metric_semantics_reviewed": true,
    "selected_table": "bot_summary_day",
    "selected_columns": ["timestamp", "client_asn", "request_host", "sum(cnt_all)"],
    "metadata_origin": "direct_hydrolix_table_metadata",
    "metadata_fingerprint": "sha256:...",
    "metadata_retrieval_identity": "hydrolix-mcp:get_table_info:bot_summary_day:<retrieved_at>",
    "merge_expressions": {
      "sum(cnt_all)": "sumMerge(`sum(cnt_all)`)"
    },
    "dimensions": ["client_asn"],
    "grouped_dimensions": ["client_asn"],
    "scope": {"request_host": "www.example.com"},
    "applied_scope_filters": {"request_host": "www.example.com"},
    "current_window": {"start": "2026-03-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
    "baseline_windows": [
      {"start": "2026-02-01T00:00:00Z", "end": "2026-03-01T00:00:00Z", "label": "previous_month"}
    ],
    "baseline_method": "single_previous_window",
    "baseline_value_semantic": "duration_normalized_to_current_window",
    "baseline_normalization": {
      "method": "scale_baseline_to_current_window_duration",
      "current_duration_seconds": 2678400,
      "baseline_duration_seconds": 2419200,
      "factor": 1.107143,
      "factor_expression": "current_duration_seconds / baseline_duration_seconds",
      "applies_to": ["baseline"]
    },
    "scope_matches_report": true,
    "windows_match_report": true,
    "baseline_method_matches_report": true,
    "computed_over_complete_grouped_scope": true,
    "computed_before_output_limit": true,
    "source_limit_applied_before_denominator": false,
    "pre_denominator_filter_applied": false,
    "limit_stage": "after_denominator"
    }
  ],
  "movers": [
    {
      "rank": 1,
      "values": {"client_asn": "12345"},
      "current": 64000,
      "baseline": 11071.43,
      "current_support_raw": 64000,
      "baseline_support_raw": 10000,
      "baseline_support_normalized": 11071.43,
      "absolute_delta": 52928.57,
      "pct_change": 478.06,
      "pct_change_guarded": false,
      "direction": "increase",
      "presence_lifecycle": "existing",
      "support_change_label": "support_increase",
      "candidate_flags": [],
      "contribution_pct": 33.08,
      "confidence": "high",
      "confidence_reasons": [
        "summary_table_used",
        "retained_dimensions_fit",
        "current_support_sufficient",
        "baseline_support_sufficient",
        "trusted_direct_mcp_wrapper_evidence",
        "contribution_consistency_valid",
        "complete_contribution_scope"
      ]
    }
  ],
  "buckets": {
    "basis": "returned_rows",
    "increasing_count": 1,
    "decreasing_count": 0,
    "existing_count": 1,
    "new_count": 0,
    "disappeared_count": 0,
    "absent_count": 0,
    "support_increase_count": 1,
    "support_decrease_count": 0,
    "support_unchanged_count": 0,
    "support_zero_both_count": 0
  },
  "not_evaluated_components": [],
  "limitations": [
    {
      "code": "aggregate_rows_only",
      "severity": "info",
      "message": "Attribution is based on pre-aggregated current and baseline rows, not raw request inspection."
    },
    {
      "code": "no_causal_claim",
      "severity": "required",
      "message": "Movers explain observed aggregate delta but do not prove cause."
    }
  ],
  "confidence": "high",
  "confidence_reasons": [
    "summary_table_used",
    "retained_dimensions_fit",
    "comparable_windows_available",
    "trusted_direct_mcp_wrapper_evidence",
    "contribution_consistency_valid",
    "complete_contribution_scope"
  ],
  "interpretation_constraints": [
    "attribution_from_aggregate_deltas",
    "movement_only",
    "no_causal_claim",
    "llm_may_summarize_structured_evidence_only"
  ]
}
```

`total_current`, `total_baseline`, `total_delta`, `total_abs_delta`, and
`buckets` describe returned rows when `output_limit_applied` is true. The report
must make that explicit with `totals_basis: "returned_rows"` and
`buckets.basis: "returned_rows"`. Complete-scope contribution evidence uses
`complete_scope_total_abs_delta`; consumers must not infer complete-scope totals
from returned-row totals. A report must not claim high confidence solely because
these fields are present; the provenance evidence and contribution consistency
checks must also pass.

The output `metric_kind` is normalized, not copied from caller input. It must
be derived from the reviewed metric allowlist entry for `metric`. If the caller
supplied a different `metric_kind`, preserve that value only under
`input_assertions` or debug metadata and add `metadata_conflict` if it affects
an attempted trust-unlocking calculation.

`presence_lifecycle` and `support_change_label` are intentionally separate.
`presence_lifecycle` answers whether an emitted mover has trusted support in
the current and baseline periods: `new`, `disappeared`, `existing`, or
`not_evaluated`. `absent` is not a v1 `movers[]` row label. It is reserved for
bucket accounting of trusted zero/zero support rows when the report has a
complete grouped scope and chooses to count them. `support_change_label`
answers how trusted support changed:
`support_increase`, `support_decrease`, `support_unchanged`,
or `not_evaluated` for emitted movers. `support_zero_both` is also bucket-only
in v1 because zero/zero rows are not ranked movers.
`bot_attribution_report.v1` should not emit the ambiguous field name
`lifecycle`; older examples that used
`lifecycle: "unchanged"` map to `presence_lifecycle: "existing"` plus
`support_change_label: "support_unchanged"` when support is positive in both
periods.

`buckets.absent_count` counts trusted zero/zero support entities only when the
input is complete enough to know those entities exist in the grouped scope and
have zero support in both periods. Such rows must not be ranked, must not
contribute to returned-row movement totals, and must not be emitted in
`movers[]` in v1. If the input does not enumerate complete zero/zero entities,
`absent_count` should be `0` or omitted according to the bucket output mode,
not inferred.

Rows excluded because period absence is not trusted are not returned rows for
v1 ranking purposes. They must not contribute to `total_current`,
`total_baseline`, `total_delta`, `total_abs_delta`, presence lifecycle bucket
counts, support-change bucket counts, or rank assignment. The report should
still expose their skipped count and entity values, when available, through
`not_evaluated_components` and a limitation so a consumer can explain that the
ranked mover list is incomplete for those entities.

Rows emitted with one-sided sparse candidate flags are returned rows for
ranking and returned-row totals when their selected metric values are present,
but their lifecycle bucket is `not_evaluated`, not `new`, `disappeared`, or
`existing`. They must not increment high-confidence lifecycle buckets. When
bucket accounting is enabled, count them only in explicit sparse-candidate or
not-evaluated buckets; otherwise leave them out of lifecycle bucket totals and
preserve the reason in `candidate_flags` and `not_evaluated_components`.

Mover ranking must be deterministic. Sort by `abs(absolute_delta) DESC`, then
by each normalized dimension value in `dimensions` order using stable string
comparison with nulls last. Assign `rank` after sorting and final output
limiting. SQL templates should include the same tie-breakers after
`abs_delta DESC`.

Attribution reports are expected to be a scorecard handoff source for
single-entity rows. Current `scorecard.py` can auto-compute missing
`contribution_pct` when it sees legacy compute-safe metadata such as
`rowset_complete: true` or `contribution_basis: "complete_scope"`. Advanced
attribution must therefore treat scorecard export as a separate contract, not a
blind copy of report metadata. It must not emit direct scorecard-compatible
legacy metadata that could cause current `scorecard.py` to recompute
contribution from returned rows for a limited `complete_scope_pre_limit` or
`provided_complete_scope` report. Once scorecard hardening is implemented and
tested in v1c, the export should be an explicit in-process command, such as
`attribution.py export-scorecard`, that validates the handoff rules, invokes
hardened scorecard generation in the same reviewed process, and emits final
scorecard artifacts. If `bot_scorecard_input.v1` exists in that flow, it is an
internal handoff object passed with `scorecard_trusted_context`, not a
standalone reusable file.

Scorecard hardening is a v1c release blocker before any advanced scorecard
export exists. `skills/bot-insights/scripts/scorecard.py` must reject direct
`bot_attribution_report.v1` input and must only accept advanced attribution
handoff during an explicit in-process export path that transforms a
high-confidence attribution report into `bot_scorecard_input.v1` with
`scorecard_export_safe: true`, digest-bound `scorecard_handoff_evidence`, and
a non-user-editable `scorecard_trusted_context` passed by reviewed code.
`schema_version: "bot_scorecard_input.v1"` plus
`scorecard_export_safe: true` is never sufficient for acceptance. A saved,
pasted, or caller-supplied `bot_scorecard_input.v1` artifact is self-attesting
JSON in v1 and must be rejected by `scorecard.py` unless a future signed
portable-evidence model exists. Until hardening is implemented and tested, no
advanced scorecard export subcommand, flag, or alternate output mode should
exist, and v1a/v1b must not emit `bot_scorecard_input.v1` or any
scorecard-safe artifact, including `scorecard_export_safe: true`. This is a
fail-closed v1 acceptance criterion: v1c is the first phase that may expose
scorecard export, and only as a reviewed in-process handoff after
`scorecard.py` hardening tests prove direct `bot_attribution_report.v1`
rejection, boolean-only `bot_scorecard_input.v1` rejection, saved/pasted
`bot_scorecard_input.v1` rejection, and separate preserve-safe and
compute-safe contribution gates.

The hardened scorecard API must make the in-process boundary concrete. The v1c
library entrypoint should accept an optional trusted-context parameter:

```python
build_artifacts(
    value,
    *,
    entity_type=None,
    min_count=100.0,
    limit=0,
    scorecard_trusted_context=None,
)
```

The CLI path, file/stdin path, and ordinary saved/pasted JSON path must always
call this entrypoint with `scorecard_trusted_context=None`. With a null context,
`scorecard.py` must reject direct `bot_attribution_report.v1` input and reject
`bot_scorecard_input.v1` input even when the JSON contains
`scorecard_export_safe: true`. A non-null `scorecard_trusted_context` may be
passed only by the reviewed attribution export code in the same Python process.
`scorecard.py` accepts an advanced handoff only when the input object is the
internal `bot_scorecard_input.v1` shape, `scorecard_export_safe: true` is present,
`scorecard_handoff_evidence` is present and digest-bound, the
`scorecard_trusted_context` validates that evidence, and the preserve-safe or
compute-safe contribution gate passes. Otherwise it must raise or return a
typed invalid-input error rather than silently falling back to legacy scorecard
behavior.

The scorecard contract must have two gates:

- Preserve gate: existing `contribution_pct` may be trusted when advanced
  evidence validates `complete_rowset`, `complete_scope_pre_limit`, or
  `provided_complete_scope`, and the percentage passes algebraic consistency
  checks.
- Compute gate: missing contribution may be locally computed only when
  `rowset_complete: true`, `contribution_basis: "complete_rowset"`, the metric
  is additive according to the reviewed v1 allowlist, and the grouped rowset is
  complete for the scorecard scope.

These gates are explicit and non-interchangeable. Preserve-safe evidence allows
`scorecard.py` to keep an existing trusted `contribution_pct`; it does not
allow reconstruction of a missing percentage. Compute-safe evidence allows
missing contribution reconstruction only for a complete rowset. A
`complete_scope_pre_limit` or `provided_complete_scope` export with missing
`contribution_pct` is unsafe and must be rejected.
In v1c, neither gate can set `scorecard_export_safe: true` unless the evidence
was delivered through the reviewed direct-MCP wrapper path and validated through
in-memory `trusted_context`; the export path also must pass an in-process
`scorecard_trusted_context` to `scorecard.py` so the scorecard script can
distinguish reviewed handoff from user-supplied JSON. Saved or pasted
attribution reports and saved or pasted scorecard input artifacts remain
scorecard-unsafe. In v1a and v1b, no path may set `scorecard_export_safe: true`
at all.

Safe scorecard in-process handoff shapes are v1c-only. V1a and v1b must not
expose an advanced scorecard export subcommand, flag, alternate output mode,
`bot_scorecard_input.v1` artifact, or scorecard-safe artifact. In v1c,
`bot_scorecard_input.v1` is an internal handoff object consumed immediately by
hardened scorecard generation with `scorecard_trusted_context`; it is not a
portable artifact accepted from file, stdin, paste, or a caller-supplied object.

Preserve-safe v1 internal scorecard handoff shape:

- The internal handoff object must include `scorecard_export_safe: true`, and
  the export path may set that marker only after contribution evidence,
  algebraic consistency, row shape, single-entity compatibility, direct-MCP
  wrapper trusted context, scorecard handoff context, and result digest binding
  have been validated.
  A caller-supplied
  `scorecard_export_safe: true` inside `input_doc` is only an assertion and
  must not be preserved into an accepted advanced handoff.
- The internal handoff object must include `scorecard_handoff_evidence`, and
  `scorecard.py` must validate it against the in-process
  `scorecard_trusted_context` before accepting the handoff. The evidence must
  bind `source_result_digest`,
  `source_query_fingerprint`, source schema, `advanced_contribution_basis`,
  exported row count, exported entity contract, baseline value semantic, and an
  `export_rows_digest` recomputed from the scorecard rows. Missing or
  mismatched handoff evidence rejects the export even when
  `scorecard_export_safe: true` is present.
- The internal handoff object must include a supported scorecard entity
  contract. Either set top-level `entity_type` to one of `scorecard.py`'s
  supported entity types and include `entity` on each row, or include a
  concrete supported entity column such as `client_asn`, `request_path_norm`,
  or `request_host` on every row.
  The recommended v1 export includes both `entity_type` and the concrete
  entity column so older and newer scorecard readers can map the row
  deterministically.
- Every exported row must already contain trusted per-row `contribution_pct`
  copied from the normalized attribution report after algebraic validation.
  Rows without trusted contribution are not preserve-safe scorecard rows.
- Exported `current`, `baseline`, `absolute_delta`, and `contribution_pct`
  must preserve the normalized report semantics. If the attribution report used
  `baseline_value_semantic: "duration_normalized_to_current_window"`, the
  scorecard export uses that normalized `baseline` value and must include the
  same baseline semantic metadata; it must not silently switch to raw-window
  baseline values.
- Exported metadata must omit legacy compute-safe fields that older
  `scorecard.py` versions may use to compute missing contribution from returned
  rows. In particular, do not export `contribution_basis: "complete_scope"` or
  `rowset_complete: true` for a limited advanced handoff unless the rowset is
  actually complete for the scorecard scope.
- Advanced evidence metadata should use names ignored by older `scorecard.py`,
  for example `advanced_contribution_basis: "complete_scope_pre_limit"` or
  `advanced_contribution_basis: "provided_complete_scope"`, plus
  `advanced_contribution_evidence`, when those fields are useful for
  downstream audit.
- If a consumer requires old compute-safe scorecard metadata, scorecard export
  must be disabled for `complete_scope_pre_limit` and
  `provided_complete_scope` until `scorecard.py` has separate preserve and
  compute gates.
- Direct `bot_attribution_report.v1` input to `scorecard.py` is invalid in v1.
  `scorecard.py` must reject it directly, and must ignore or reject advanced
  attribution metadata unless `scorecard_export_safe: true` is present on a
  `bot_scorecard_input.v1` internal handoff with valid digest-bound
  `scorecard_handoff_evidence` and the reviewed in-process
  `scorecard_trusted_context`.

Compute-safe complete-rowset internal scorecard handoff shape:

- This shape is allowed only after the hardened v1c `scorecard.py` can prove
  compute safety from the internal `bot_scorecard_input.v1` handoff without
  accepting direct `bot_attribution_report.v1` or saved/pasted scorecard input.
- The internal handoff object may omit per-row `contribution_pct` only when
  `advanced_contribution_basis: "complete_rowset"`, the metric is additive
  according to the reviewed allowlist, the exported rows are single-entity
  compatible, and trusted `complete_rowset_evidence` proves every grouped row
  for the scorecard scope is present before any source, group, denominator, or
  output limit.
- The internal handoff object must include enough validated evidence for
  `scorecard.py` to recompute `total_abs_delta` over the exported complete
  rowset and to reject the handoff if rowset completeness, metric semantics,
  baseline semantics, scope, windows, or digest binding are missing or
  mismatched.
- `complete_scope_pre_limit` and `provided_complete_scope` must never use this
  compute-safe shape; they are preserve-safe only and require existing trusted
  per-row `contribution_pct`.

Minimal preserve-safe internal handoff example:

```json
{
  "schema_version": "bot_scorecard_input.v1",
  "scorecard_export_safe": true,
  "advanced_source_schema": "bot_attribution_report.v1",
  "advanced_contribution_basis": "complete_scope_pre_limit",
  "scorecard_handoff_evidence": {
    "source_schema": "bot_attribution_report.v1",
    "source_confidence": "high",
    "source_query_fingerprint": "sha256:...",
    "source_result_digest": "sha256:...",
    "advanced_contribution_evidence_source": "trusted_template_generator",
    "export_rows_digest": "sha256:...",
    "exported_row_count": 1,
    "entity_contract": {
      "entity_type": "client_asn",
      "entity_field": "client_asn"
    }
  },
  "baseline_value_semantic": "duration_normalized_to_current_window",
  "entity_type": "client_asn",
  "rows": [
    {
      "entity_type": "client_asn",
      "client_asn": "12345",
      "entity": "12345",
      "current": 64000,
      "baseline": 11071.43,
      "absolute_delta": 52928.57,
      "contribution_pct": 33.08
    }
  ]
}
```

This shape intentionally does not map `complete_scope_pre_limit` or
`provided_complete_scope` to legacy `contribution_basis: "complete_scope"`.
It preserves the normalized baseline and contribution values from the
attribution report instead of changing to raw-window scorecard semantics. The
example shows the internal handoff shape only; saved or pasted JSON with this
shape remains caller-supplied input and must be rejected without the in-process
`scorecard_trusted_context`.

### Composite Dimensions

Composite dimensions use the same schema. Only `dimensions` and mover `values`
change. This fragment is abbreviated and is not standalone
high-confidence-unlocking. A full report may carry high confidence only when it
also includes the complete trusted evidence object from the canonical example
and that evidence arrived through the direct-MCP v1 skill-controlled boundary:

```json
{
  "dimensions": ["request_path_norm", "bot_class"],
  "movers": [
    {
      "rank": 1,
      "values": {
        "request_path_norm": "/api/search",
        "bot_class": "good"
      },
      "current": 42000,
      "baseline": 2000,
      "current_support_raw": 42000,
      "baseline_support_raw": 2000,
      "absolute_delta": 40000,
      "pct_change": 2000,
      "pct_change_guarded": false,
      "direction": "increase",
      "presence_lifecycle": "existing",
      "support_change_label": "support_increase",
      "candidate_flags": [],
      "contribution_pct": 25,
      "confidence": "high",
      "confidence_reasons": [
        "summary_table_used",
        "trusted_direct_mcp_wrapper_evidence",
        "contribution_consistency_valid",
        "complete_contribution_scope"
      ]
    }
  ]
}
```

Composite attribution is not first-class scorecard input in v1. `scorecard.py`
is single-entity oriented, so a v1 scorecard export must skip composite movers
and add a `composite_scorecard_export_not_supported` limitation when a caller
asks for scorecard rows from a composite attribution report. The attribution
report itself remains valid and may be summarized directly. A later version may
introduce a deterministic scorecard representation such as
`entity_type: "composite"`, an escaped `entity_key`, and structured
`entity_values`, but v1 must not invent that shape implicitly.

### Limitations

`limitations` must always be present. It may be empty, but v1 should normally
include at least the generic aggregate and non-causal limitations.

`limitations` records what this artifact could not prove or evaluate.
`interpretation_constraints` records what consumers must not infer.

Recommended limitation shape:

```json
{
  "code": "complete_scope_not_proven",
  "severity": "warning",
  "message": "Contribution percentage was withheld because the input did not prove complete grouped scope.",
  "applies_to": ["contribution_pct"],
  "future_improvement": "Compute contribution over the full grouped scope in Hydrolix before applying LIMIT."
}
```

Recommended limitation severities:

- `info`: contextual limitation that should be visible but does not reduce
  correctness.
- `warning`: material limitation that affects interpretation or confidence.
- `required`: constraint that every consumer must honor.

Recommended v1 limitation codes:

- `aggregate_rows_only`
- `no_causal_claim`
- `single_metric_only`
- `single_dimension_set_only`
- `non_additive_metric_contribution_withheld`
- `complete_scope_not_proven`
- `complete_scope_denominator_invalid`
- `caller_asserted_complete_scope_not_trusted`
- `trusted_context_missing`
- `trusted_context_invalid`
- `trusted_context_digest_mismatch`
- `trusted_evidence_missing`
- `trusted_evidence_mismatch`
- `metadata_conflict`
- `metadata_fingerprint_missing`
- `metadata_fingerprint_mismatch`
- `query_fingerprint_missing`
- `result_digest_missing`
- `duplicate_entity_period_key`
- `duplicate_aggregation_not_trusted`
- `invalid_numeric_value`
- `negative_support_count`
- `invalid_contribution_pct`
- `provided_contribution_inconsistent`
- `metadata_poor_input`
- `dimensions_inferred`
- `dimension_inference_ambiguous`
- `limited_rowset`
- `contribution_withheld`
- `period_absence_not_trusted`
- `lifecycle_not_evaluated`
- `lifecycle_support_missing`
- `composite_scorecard_export_not_supported`
- `baseline_method_missing`
- `baseline_windows_not_reduced`
- `unsupported_summary_dimension_set`
- `unsupported_summary_filter`
- `calendar_length_difference_not_normalized`
- `asn_path_summary_not_available`
- `asn_path_cardinality_not_validated`
- `zero_baseline_guard`
- `subunit_baseline_guard`
- `sparse_counts`
- `partial_current_bucket`
- `missing_retained_dimension`
- `raw_table_fallback`
- `source_coverage_caveat`
- `multi_step_attribution_not_evaluated`

When contribution cannot be safely emitted:

```json
{
  "contribution_pct": null,
  "not_evaluated_components": [
    {
      "name": "contribution_pct",
      "reason": "complete_scope_not_proven",
      "required_metadata": [
        "trusted evidence for rowset_complete: true and contribution_basis: complete_rowset",
        "or contribution_basis: complete_scope_pre_limit with trusted complete_scope_pre_limit_evidence, complete_scope_total_abs_delta or identical per-row denominator, per-row contribution_pct, computed_before_output_limit: true, same-scope no-source-limit evidence, and contribution consistency within tolerance",
        "or contribution_basis: provided_complete_scope with trusted provided_contribution_evidence proving same metric, dimensions, scope, windows, baseline method, baseline value semantic, any baseline normalization metadata, no pre-denominator filtering, and contribution consistency within tolerance"
      ]
    }
  ],
  "limitations": [
    {
      "code": "contribution_withheld",
      "severity": "warning",
      "message": "Contribution percentage was not computed from a limited or incomplete rowset."
    }
  ]
}
```

## Confidence And Guardrails

Confidence is a label plus machine-readable reasons.

Confidence is an analytical quality label, not an origin/authenticity label.
`confidence: "high"` means the normalizer saw internally consistent,
skill-controlled evidence for the requested calculation through a valid
direct-MCP `trusted_context`. It does not prove that the process, Python
caller, MCP server, or Hydrolix result was tamper-resistant.
Consumers that need stronger origin guarantees must wait for a later
signed-evidence or isolation model.

Labels:

- `high`: valid in-memory `trusted_context` from the reviewed direct-MCP
  runtime wrapper is present for this invocation; summary table used, retained
  grouped dimensions and scope/filter columns fit, comparable windows are
  available, baseline value semantics are explicit and any normalization
  metadata is trusted, support satisfies the lifecycle-specific rules below,
  granularity matches the comparison type, raw fallback is not used, and any
  emitted complete-scope contribution or lifecycle absence is backed by
  direct-MCP wrapper evidence in `trusted_context.trusted_evidence` and passes
  consistency validation. The valid direct-MCP trusted context is required even
  when the high report or mover emits no contribution percentage and no absence
  claim.
- `medium`: summary table used but one report-level caveat exists, such as
  fallback baseline, source coverage caveat, lifecycle not evaluated for a
  non-core subset, caller-asserted but otherwise internally consistent
  complete-scope metadata, or missing optional analysis. Raw fallback may be
  `medium` only when explicit source coverage evidence establishes that the raw
  aggregate rows cover the same scope, windows, dimensions, and metric semantics
  as the requested report.
- `low`: sparse counts, partial current bucket, raw-table fallback, missing
  retained dimension, metadata-poor MCP input, plain pasted JSON, mixed source
  coverage materially affects the metric, invalid provided contribution
  consistency, or limited rowset prevents core calculations.

Confidence downgrade behavior must be deterministic:

- Hard-low conditions set report confidence to `low`: metadata-poor MCP input
  without a metadata sidecar, plain manual paste, malformed optional evidence
  that invalidates a requested core calculation, invalid contribution
  consistency for a supplied percentage, unsupported retained dimensions, raw
  fallback without trusted source coverage evidence, or untrusted absence needed
  for the requested lifecycle result.
- Hard-medium caps prevent `high` but may allow `medium`: missing or invalid
  direct-MCP `trusted_context` on an otherwise internally consistent
  standalone/public JSON report, caller-asserted complete-scope metadata that
  is internally consistent, missing provenance for otherwise complete optional
  metadata, lifecycle not evaluated for a non-core subset, fallback baseline
  with comparable windows, or raw fallback with trusted source coverage
  evidence.
- High requirements are all-or-nothing: summary-backed retained grouped
  dimensions and summary-backed retained scope/filter columns, comparable
  windows, matching granularity, reviewed metric semantics, explicit baseline
  value semantics with trusted normalization metadata when applicable, valid
  in-memory direct-MCP `trusted_context` for the invocation, matching
  `query_fingerprint`, recomputed `result_digest`, trusted generator evidence
  for any emitted complete-scope contribution or lifecycle absence, valid
  denominator stage when contribution is emitted, contribution consistency
  within tolerance when contribution is emitted, no raw fallback, no
  metadata-poor input, and no warning limitation that applies to a core emitted
  field.
- Lifecycle-specific high requirements:
  - `presence_lifecycle: "new"` may be high only when current support is at
    least `min_count` using raw observed support and baseline support is exact
    trusted zero or trusted
    absence for the same metric, dimensions, scope, windows, and baseline
    method.
  - `presence_lifecycle: "disappeared"` may be high only when baseline support
    is at least `min_count` using raw observed support and current support is
    exact trusted zero or trusted absence for the same metric, dimensions,
    scope, windows, and baseline method.
  - `presence_lifecycle: "existing"` may be high only when current and
    baseline support are both positive and both meet the configured support
    threshold. Positive-but-sparse support on both sides remains `existing`,
    but the mover should carry sparse support flags and cannot use lifecycle
    support as a high-confidence reason. One-sided support below `min_count`
    emits `presence_lifecycle: "not_evaluated"` with sparse candidate flags
    and no high-confidence lifecycle reason.
  - `absent` is not a mover lifecycle in v1. Trusted zero/zero support rows
    may increment `buckets.absent_count` and
    `buckets.support_zero_both_count` when bucket accounting is complete enough
    to know them, but they are not ranked and are not emitted in `movers[]`.

Reasons inherited from existing posture and scorecard behavior:

- `summary_table_used`
- `raw_table_fallback`
- `retained_dimensions_fit`
- `missing_retained_dimension`
- `comparable_windows_available`
- `fallback_baseline_selected`
- `granularity_matches_comparison`
- `granularity_mismatch`
- `current_support_sufficient`
- `baseline_support_sufficient`
- `sparse_counts`
- `partial_current_bucket`
- `source_coverage_caveat`
- `zero_baseline_guard`
- `subunit_baseline_guard`

New reasons for attribution:

- `complete_contribution_scope`
- `limited_rowset_incomplete`
- `contribution_withheld`
- `provided_contribution_used`
- `computed_contribution_from_complete_scope`
- `trusted_direct_mcp_wrapper_evidence`
- `caller_assertion_not_trusted`
- `manual_paste_metadata_poor`
- `metadata_poor_input`
- `contribution_consistency_valid`
- `provided_contribution_inconsistent`
- `metric_input_missing`
- `ambiguous_metric_input`
- `dimension_input_missing`
- `dimensions_inferred`
- `dimension_inference_ambiguous`
- `baseline_method_missing`
- `new_entity_zero_baseline_support`
- `disappeared_entity_zero_current_support`
- `trusted_baseline_absence`
- `trusted_current_absence`
- `support_increase`
- `support_decrease`
- `support_unchanged`
- `pct_change_guarded`
- `pct_change_not_defined_for_negative_metric`
- `period_absence_not_trusted`
- `lifecycle_not_evaluated`
- `lifecycle_support_missing`
- `multi_dimension_composite_key`
- `additive_metric_contribution_supported`
- `non_additive_metric_contribution_withheld`
- `summary_dimension_set_supported`
- `unsupported_summary_dimension_set`
- `unsupported_summary_filter`

Rules:

- Compute `absolute_delta = current - baseline`.
- For reviewed non-negative metrics where the baseline guard is meaningful,
  compute `pct_change = absolute_delta / greatest(baseline, 1) * 100`.
  Set `pct_change_guarded: true` when `baseline < 1`.
- For reviewed metrics that can be negative, v1 must omit or emit
  `pct_change: null` and add
  `pct_change_not_defined_for_negative_metric`, unless the metric allowlist
  defines an exact reviewed metric-specific percentage-change formula. A caller
  cannot enable percentage-change math for negative-valued metrics through
  metadata.
- Add `zero_baseline_guard` only when the normalized baseline metric value is
  exact zero or trusted absence filled to zero. Add `subunit_baseline_guard`
  when `0 < baseline < 1`; this usually indicates a normalized multi-window
  baseline and must not be treated as absence.
- Set `direction` from the sign of `absolute_delta` only when both current and
  baseline values are present or safely zero-filled.
- Classify `presence_lifecycle` from `current_support_raw`,
  `baseline_support_raw`, and period-presence evidence only. Do not infer
  presence lifecycle from arbitrary selected metric values unless the selected
  metric is a reviewed additive entity-volume count in the v1 allowlist.
- Classify `presence_lifecycle` only after lifecycle support has been resolved.
  Validate period absence only when the row needs absence or zero-fill to decide
  the lifecycle result. Positive current and positive baseline support classify
  as `existing` without absence evidence. Default v1 excludes rows when a
  required metric side is missing because zero-fill is not trusted. Use
  `not_evaluated` for emitted rows whose selected metric values are both
  present but lifecycle/support cannot be evaluated, such as non-volume metrics
  without required support fields, or behind a future explicit
  include-not-evaluated option for unsafe one-sided rows.
- Classify `presence_lifecycle` for emitted movers in this precedence order,
  after default exclusion of unsafe one-sided rows:
  `not_evaluated` when lifecycle/support is unsupported but metric values are
  present;
  `new` when `baseline_support_raw == 0` by exact trusted zero/absence and
  `current_support_raw >= min_count`; `disappeared` when
  `current_support_raw == 0` by exact trusted zero/absence and
  `baseline_support_raw >= min_count`; `existing` when both
  `current_support_raw > 0` and `baseline_support_raw > 0`; `not_evaluated`
  when support is one-sided but the positive side is below `min_count` and the
  row is emitted with a sparse candidate flag; zero/zero support rows are
  excluded from movers and counted only in absent/support-zero-both buckets
  when bucket evidence is complete. Equal positive support is
  `presence_lifecycle: "existing"`, not `unchanged`.
- Classify `support_change_label` separately after support values are resolved:
  `not_evaluated` when an emitted row has metric values but support is missing
  or unsupported; unsafe one-sided rows are excluded by default before support
  label assignment. Zero/zero support rows are excluded from movers and counted
  only in support-zero-both buckets when bucket evidence is complete;
  `support_increase` when
  `current_support_raw > baseline_support_raw`; `support_decrease` when
  `current_support_raw < baseline_support_raw`; and `support_unchanged` when
  positive raw support values are equal. Metric deltas and `direction` may still show
  movement for a row whose support change label is `support_unchanged`, because
  selected metric movement and support movement are separate concepts.
- Emit sparse lifecycle candidates separately in `candidate_flags`, not by
  overloading lifecycle or confidence. Use `sparse_new_candidate` when
  `baseline_support_raw == 0` by exact trusted zero/absence and `0 <
  current_support_raw < min_count`; use `sparse_disappeared_candidate` when
  `current_support_raw == 0` by exact trusted zero/absence and `0 <
  baseline_support_raw < min_count`; use `below_support_threshold` when both
  raw support values are nonzero but below support. These flags mean "possible
  lifecycle movement below support threshold"; they do not assert `new`,
  `disappeared`, or high-confidence `existing` presence evidence. When such a
  row is emitted, set `presence_lifecycle: "not_evaluated"`, omit
  high-confidence lifecycle reasons such as `new_entity_zero_baseline_support`
  or `disappeared_entity_zero_current_support`, and add `sparse_counts` or the
  specific sparse candidate reason. Do not emit sparse lifecycle candidate
  flags for non-volume selected metrics unless support fields are present.
- Treat ramp-up and ramp-down as existing movers, not lifecycle transitions:
  `baseline_support_raw > 0` but below `min_count` with
  `current_support_raw >= min_count` is `existing` with
  `sparse_baseline_support`; `current_support_raw > 0` but below `min_count`
  with `baseline_support_raw >= min_count` is `existing` with
  `sparse_current_support`. Fractional positive normalized display support,
  including `0 < baseline_support_normalized < 1`, follows this sparse
  existing rule rather than the `new` or `disappeared` zero boundary; raw
  support remains the threshold basis.
- Do not compute `contribution_pct` unless complete scope is backed by trusted
  evidence. Do not preserve supplied complete-scope `contribution_pct` unless
  trusted evidence is present and the supplied percentage is algebraically
  consistent with `absolute_delta` and `complete_scope_total_abs_delta`.
- Caller-asserted complete-scope metadata may be echoed for debugging but must
  add `caller_asserted_complete_scope_not_trusted`, must cap confidence below
  `high`, and must not emit trusted `contribution_pct`.
- Do not compute `contribution_pct` for non-additive metrics unless explicit
  reviewed semantics for that exact metric are present in the v1 allowlist.
- If `complete_scope_total_abs_delta` is zero, omit or null
  `contribution_pct`; do not divide by `greatest(..., 1)` for contribution
  preservation.
- Do not infer missing metric values as zero.
- Do not infer lifecycle absence from numeric zero values in any row shape
  unless trusted `zero_fill_evidence.period_value_trust` proves that period
  side.
- For period-split input, the normalizer may treat a missing opposite-period
  entity row as zero only after grouping by the requested dimensions and only
  when trusted `zero_fill_evidence.period_value_trust` evidence is valid for
  the needed side. A present row with a missing metric value remains invalid
  and must not be interpreted as zero.
- For period-split input, local duplicate aggregation is rejected by default.
  It is allowed only when trusted duplicate-aggregation evidence proves
  disjoint partitioning/source grouping semantics for the duplicate
  entity/period keys. Additive metric semantics alone are not sufficient.

Recommended `candidate_flags` values:

- `sparse_new_candidate`: current support is present but below the support
  threshold and the baseline support side is exact trusted zero or absence.
  Emitted rows with this flag use `presence_lifecycle: "not_evaluated"`.
- `sparse_disappeared_candidate`: baseline support is present but below the
  support threshold and the current support side is exact trusted zero or
  absence. Emitted rows with this flag use
  `presence_lifecycle: "not_evaluated"`.
- `below_support_threshold`: both sides are present but below the support
  threshold and the mover should not be treated as high-confidence lifecycle
  evidence.
- `sparse_baseline_support`: baseline support is nonzero but below the support
  threshold while current meets support, so the mover is a ramp-up existing
  entity rather than `new`.
- `sparse_current_support`: current support is nonzero but below the support
  threshold while baseline meets support, so the mover is a ramp-down existing
  entity rather than `disappeared`.

Advanced attribution is intentionally stricter than the legacy posture mover
path. A zero-baseline percentage change should add `zero_baseline_guard` and
`pct_change_guarded`, but that guard applies to percentage-change
interpretation, not necessarily to aggregate mover confidence. A `new` entity
with `presence_lifecycle: "new"`, sufficient current support, trusted
absence/zero baseline support evidence, retained dimension fit, comparable
windows, complete contribution scope when contribution is emitted, and valid
direct-MCP `trusted_context` may still have high aggregate confidence.
Consumers must still treat its percent change as guarded because the
denominator was zero. A fractional positive baseline should add
`subunit_baseline_guard` and remain `presence_lifecycle: "existing"` with a
sparse support flag; it is not evidence of a zero baseline.

Lifecycle-specific confidence examples:

```json
{
  "presence_lifecycle": "new",
  "support_change_label": "support_increase",
  "current_support_raw": 64000,
  "baseline_support_raw": 0,
  "confidence": "high",
  "confidence_reasons": [
    "current_support_sufficient",
    "new_entity_zero_baseline_support",
    "trusted_baseline_absence",
    "trusted_direct_mcp_wrapper_evidence"
  ]
}
```

```json
{
  "presence_lifecycle": "disappeared",
  "support_change_label": "support_decrease",
  "current_support_raw": 0,
  "baseline_support_raw": 12500,
  "confidence": "high",
  "confidence_reasons": [
    "baseline_support_sufficient",
    "disappeared_entity_zero_current_support",
    "trusted_current_absence",
    "trusted_direct_mcp_wrapper_evidence"
  ]
}
```

```json
{
  "presence_lifecycle": "existing",
  "support_change_label": "support_unchanged",
  "current_support_raw": 10000,
  "baseline_support_raw": 10000,
  "confidence": "high",
  "confidence_reasons": [
    "current_support_sufficient",
    "baseline_support_sufficient",
    "support_unchanged",
    "trusted_direct_mcp_wrapper_evidence"
  ]
}
```

## Summary-Dimension Constraints

V1 must validate requested dimensions against the retained dimensions of the
selected summary surface. In addition to the universal direct-MCP
`trusted_context` prerequisite, a report may have high confidence only when all
requested grouped dimensions and every requested scope or filter column are
retained by the selected summary table. If a summary table supports the grouped
dimension but not a filter column, the summary filter is unsupported because
the aggregate population cannot be proven to match the requested report. The
normalizer must add `unsupported_summary_filter` for that case and must cap
confidence below high unless trusted raw fallback coverage evidence proves the
same metric, windows, grouped dimensions, filters, and population. A reviewed
raw-table fallback may still be valid, but it should be marked as fallback and
should be `low` by default. It may rise to `medium` only with explicit
raw-source coverage evidence, and it must not be `high` in v1.

When an unsupported summary filter affects requested output, also add a
`not_evaluated_components` entry naming the filter column and the selected
summary table.

Current summary-backed non-SIEM dimension sets:

- `bot_summary_{minute,hour,day}` retains `request_host`, `hdx_cdn`,
  `bot_class`, `ai_category`, `is_bot_traffic`, `client_asn`, `asn_type`,
  `resource_category`, and `request_method`.
- `bot_agg_hour` retains `request_host`.
- `bot_agg_path_{minute,hour,day}` retains `request_host`,
  `request_path_norm`, `bot_class`, and `asn_type`.
- `bot_agg_asn_hour` retains `request_host`, `client_asn`, and `asn_type`.
- `bot_agg_traffic_hour` retains `request_host`, `is_bot_traffic`, and
  `ai_category`.
- `bot_agg_ua_hour` retains `request_host` and `bot_class`.
- `bot_agg_resource_{minute,hour,day}` retains `request_host` and
  `resource_category`.

Current SIEM summary-backed dimension sets:

- `bot_siem_summary_{minute,hour,day}` retains `request_host`,
  `action_taken`, `client_asn`, and `policy_id`.
- `bot_siem_filter_summary_{minute,hour,day}` retains `request_host`,
  `client_asn`, `is_bot_traffic`, `ai_category`, and `resource_category`.
- `bot_siem_class_{minute,hour,day}` retains `request_host`, `client_asn`,
  and `akamai_canonical_bot_class`.

Important request-path constraints:

- `request_path_norm` is summary-backed through `bot_agg_path_*`.
- `request_path_norm + bot_class` is summary-backed.
- `request_path_norm + asn_type` is summary-backed.
- `request_path_norm + client_asn` is not summary-backed today.
- `request_path_norm + ai_category` is not summary-backed today.

If future work adds `bot_agg_asn_path_hour` or `bot_agg_asn_path_day`, the
design should treat that as a catalog/schema expansion. It is not automatically
too high-cardinality, but it is cardinality-risky and should be validated by
comparing observed grouped row counts for `request_host + client_asn +
request_path_norm` against existing path and ASN summary shapes over
representative windows. Avoid adding extra retained dimensions such as
`bot_class`, `ai_category`, `is_bot_traffic`, or `resource_category` to that
summary until data proves they are needed.

Raw-table fallback is low confidence by default. It may be raised to medium
only when the input includes explicit source coverage evidence that the fallback
aggregate rows cover the same scope, windows, dimensions, filters, and selected
metric semantics as the requested report. Raw fallback must not produce high
confidence in v1, even when other metadata is complete.

Recommended typed raw fallback evidence list entry:

```json
{
  "evidence_id": "raw-fallback-aggregate-v1",
    "evidence_type": "raw_fallback_coverage_evidence",
    "applies_to": {"scope": "report"},
    "evidence_source": "trusted_template_generator",
    "generator_name": "bot-insights-attribution-sql",
    "generator_version": "1.0.0",
    "template_id": "raw_fallback_aggregate_v1",
    "query_fingerprint": "sha256:...",
    "result_digest": "sha256:...",
    "source_table": "bot_detection",
    "selected_table": "bot_detection",
    "selected_columns": [
      "timestamp",
      "request_host",
      "client_asn",
      "request_path"
    ],
    "metadata_origin": "direct_hydrolix_table_metadata",
    "metadata_fingerprint": "sha256:...",
    "metadata_retrieval_identity": "hydrolix-mcp:get_table_info:bot_detection:<retrieved_at>",
    "merge_expressions": {},
    "metric_expression": "count()",
    "request_level_metric_expressions": {
      "requests": "count()"
    },
    "filters": {"request_host": "www.example.com"},
    "dimensions": ["client_asn"],
    "grouped_dimensions": ["client_asn"],
    "applied_scope_filters": {"request_host": "www.example.com"},
    "current_window": {"start": "2026-03-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
    "baseline_windows": [
      {"start": "2026-02-01T00:00:00Z", "end": "2026-03-01T00:00:00Z", "label": "previous_month"}
    ],
    "baseline_method": "single_previous_window",
    "scope_matches_report": true,
    "windows_match_report": true,
    "dimensions_match_report": true,
    "metric": "requests",
    "metric_semantics_reviewed": true,
    "source_coverage_caveats": [],
    "same_population_as_requested_report": true
}
```

To raise confidence from low to medium, the evidence must identify the source
table, filters, report scope, current and baseline windows, dimensions, metric
semantics, known coverage caveats, and whether the fallback aggregate covers
the same population as the requested report. Missing or false fields keep raw
fallback at low confidence.

Raw fallback evidence for request-level tables must use request-table columns
and reviewed aggregate expressions. Summary metric aliases such as `cnt_all`,
`cnt_2xx`, `cnt_4xx`, `cnt_429`, and `cnt_5xx` belong to summary-table
templates. They must not appear as selected request-level columns for
`bot_detection` fallback unless Hydrolix metadata proves the raw table actually
has those plain columns and the reviewed fallback template intentionally uses
them. For request counts, the default reviewed request-level expression is
`count()`.

## Summary-Table SQL Patterns

These patterns belong in reference docs. They intentionally omit clients,
credentials, and execution logic.

The SQL examples are illustrative for tables whose metric columns are plain
numeric values. They must not be copied blindly into deployments where summary
columns are aggregate states. If Hydrolix metadata reports
`AggregateFunction` or `SimpleAggregateFunction` columns, use the exact
metadata-derived merge expression for that column instead of plain `sum(...)`.
Production templates should substitute expressions such as
`<current_metric_expr>` and `<baseline_metric_expr>` that were built from table
metadata before the query is run. These placeholders must be period-scoped:
`<current_metric_expr>` and `<current_support_expr>` aggregate only rows in the
current window, while `<baseline_metric_expr>` and `<baseline_support_expr>`
aggregate only rows in the baseline window or pre-reduced baseline window set.
The examples below use separate `current_by_entity` and `baseline_by_entity`
CTEs joined by entity key to make that scoping explicit.

Do not substitute the same plain `sum(...)`, `sumMerge(...)`, or other
unfiltered aggregate over the combined current-plus-baseline rowset for both
`<current_metric_expr>` and `<baseline_metric_expr>`. That is invalid because it
makes current and baseline aggregate the same population. The same rule applies
to `<current_support_expr>` and `<baseline_support_expr>`.

Window predicates must explicitly select only the current and baseline periods.
Do not use a broad `timestamp >= baseline_start AND timestamp < current_end`
predicate unless the windows are known to be adjacent and the query also
documents that no gap is being scanned. The safe default is:

```sql
WHERE (
    timestamp >= current_start AND timestamp < current_end
  )
  OR (
    timestamp >= baseline_start AND timestamp < baseline_end
  )
```

For non-adjacent baselines, this prevents scanning irrelevant gap traffic
between the baseline and current windows. Period-scoped CTEs may use separate
current and baseline `WHERE` clauses instead of one combined `OR` predicate.
For multiple baseline windows, build an explicit `OR` branch for each baseline
window or join against a small window table, then reduce those windows according
to `baseline_method` before rows reach the normalizer. Templates must emit
`baseline_value_semantic`; when they duration-normalize a baseline, they must
emit the normalization factor and formula used so March-versus-February examples
do not accidentally attribute calendar-length differences as traffic movement.

Lifecycle-friendly templates must emit enough data for the local normalizer to
classify support, but the SQL result alone is not trusted lifecycle absence
evidence. The direct-MCP wrapper must attach generator-produced
`zero_fill_evidence` metadata with nested `period_value_trust` proving that
zero-valued period support came from a complete grouped scope or trusted
full-scope join for the same metric, dimensions, scope, windows, and baseline
method. Without that wrapper evidence, `current_support_raw: 0` or
`baseline_support_raw: 0` is only a numeric row value and cannot unlock
`presence_lifecycle: "new"` or `presence_lifecycle: "disappeared"`.

The generator should produce evidence like this when a full-scope joined query
produces zero-filled period values before output limiting. It is
trust-unlocking only when the direct-MCP wrapper places a matching typed
`evidence_type: "zero_fill_evidence"` list entry under
`trusted_context.trusted_evidence`, with matching `applies_to`, and binds it to
the recomputed `result_digest`; saved sidecar JSON remains assertion-only. The
example below shows the list entry itself:

```json
{
  "evidence_id": "zero-fill-full-scope-join-v1",
    "evidence_type": "zero_fill_evidence",
    "applies_to": {"scope": "report"},
    "evidence_source": "trusted_template_generator",
    "generator_name": "bot-insights-attribution-sql",
    "generator_version": "1.0.0",
    "template_id": "full_scope_joined_pre_limit_v1",
    "query_fingerprint": "sha256:...",
    "result_digest": "sha256:...",
    "period_value_trust": {
      "current": "trusted_full_scope_join",
      "baseline": "trusted_full_scope_join"
    },
    "metric": "requests",
    "metric_expression": "<metadata_derived_metric_expr>",
    "support_expression": "<metadata_derived_support_expr>",
    "metric_semantics_reviewed": true,
    "selected_table": "bot_summary_day",
    "selected_columns": ["timestamp", "client_asn", "request_host", "sum(cnt_all)"],
    "metadata_origin": "direct_hydrolix_table_metadata",
    "metadata_fingerprint": "sha256:...",
    "metadata_retrieval_identity": "hydrolix-mcp:get_table_info:bot_summary_day:<retrieved_at>",
    "merge_expressions": {
      "sum(cnt_all)": "sumMerge(`sum(cnt_all)`)"
    },
    "dimensions": ["client_asn"],
    "grouped_dimensions": ["client_asn"],
    "scope": {"request_host": "www.example.com"},
    "applied_scope_filters": {"request_host": "www.example.com"},
    "current_window": {"start": "<current_start>", "end": "<current_end>"},
    "baseline_windows": [
      {"start": "<baseline_start>", "end": "<baseline_end>", "label": "previous_window"}
    ],
    "baseline_method": "single_previous_window",
    "baseline_value_semantic": "duration_normalized_to_current_window",
    "baseline_normalization": {
      "method": "scale_baseline_to_current_window_duration",
      "current_duration_seconds": 2678400,
      "baseline_duration_seconds": 2419200,
      "factor": 1.107143,
      "factor_expression": "current_duration_seconds / baseline_duration_seconds",
      "applies_to": ["baseline"]
    },
    "scope_matches_report": true,
    "windows_match_report": true,
    "baseline_method_matches_report": true,
    "grouped_scope_complete": true,
    "full_scope_joined_grouped_rowset": true,
    "computed_before_output_limit": true,
    "source_limit_applied_before_zero_fill": false,
    "limit_stage": "after_denominator"
}
```

### Single-Dimension Complete-Scope Attribution

These SQL blocks are template patterns. For Hydrolix summary tables backed by
aggregate-state columns, the generator must substitute
`<current_metric_expr>`, `<baseline_metric_expr>`, `<current_support_expr>`,
and `<baseline_support_expr>` with metadata-derived merge expressions over the
actual selected aggregate-state column names, for example expressions derived
from `sumMerge(\`sum(cnt_all)\`)`. They are raw copy-paste SQL only when the
selected columns are plain numeric helper columns and the placeholders are
expanded accordingly.

```sql
WITH
  toDateTime('<current_start>') AS current_start,
  toDateTime('<current_end>') AS current_end,
  toDateTime('<baseline_start>') AS baseline_start,
  toDateTime('<baseline_end>') AS baseline_end,
  dateDiff('second', current_start, current_end) AS current_duration_seconds,
  dateDiff('second', baseline_start, baseline_end) AS baseline_duration_seconds,
  toFloat64(current_duration_seconds) / nullIf(baseline_duration_seconds, 0) AS baseline_normalization_factor,
  current_by_entity AS (
    SELECT
      client_asn,
      <current_metric_expr> AS current_requests,
      <current_support_expr> AS current_support_raw
    FROM <project>.bot_summary_day
    WHERE <time_column> >= current_start
      AND <time_column> < current_end
      AND request_host = '<host>'
    GROUP BY client_asn
  ),
  baseline_by_entity AS (
    SELECT
      client_asn,
      <baseline_metric_expr> AS baseline_raw_requests,
      <baseline_support_expr> AS baseline_support_raw
    FROM <project>.bot_summary_day
    WHERE <time_column> >= baseline_start
      AND <time_column> < baseline_end
      AND request_host = '<host>'
    GROUP BY client_asn
  ),
  by_entity AS (
    SELECT
      coalesce(c.client_asn, b.client_asn) AS client_asn,
      coalesce(c.current_requests, 0) AS current_requests,
      coalesce(b.baseline_raw_requests, 0) AS baseline_raw_requests,
      coalesce(b.baseline_raw_requests, 0) * baseline_normalization_factor AS baseline_requests,
      coalesce(c.current_support_raw, 0) AS current_support_raw,
      coalesce(b.baseline_support_raw, 0) AS baseline_support_raw
    FROM current_by_entity AS c
    FULL OUTER JOIN baseline_by_entity AS b USING (client_asn)
  ),
  scored AS (
    SELECT
      *,
      current_requests - baseline_requests AS absolute_delta,
      abs(current_requests - baseline_requests) AS abs_delta,
      sum(abs(current_requests - baseline_requests)) OVER () AS total_abs_delta
    FROM by_entity
  )
SELECT
  client_asn,
  current_requests,
  baseline_raw_requests,
  baseline_requests,
  current_support_raw,
  baseline_support_raw,
  'duration_normalized_to_current_window' AS baseline_value_semantic,
  baseline_normalization_factor,
  absolute_delta,
  total_abs_delta AS complete_scope_total_abs_delta,
  <pct_change_expr_or_null> AS pct_change,
  if(total_abs_delta = 0, NULL, round(abs_delta / total_abs_delta * 100, 2)) AS contribution_pct
FROM scored
ORDER BY abs_delta DESC, toString(client_asn) ASC
LIMIT 50
```

The contribution basis is complete because `total_abs_delta` and
`contribution_pct` are computed before the final `LIMIT`. The returned
`complete_scope_total_abs_delta` appears per row; a wrapper may also promote it
to top-level metadata. If the caller applies additional lifecycle or
support-threshold pruning, it must happen after this denominator has been
computed.

`<pct_change_expr_or_null>` expands to the standard guarded percentage-change
formula only for reviewed non-negative metrics. For metrics that can be
negative, it expands to `NULL` plus the
`pct_change_not_defined_for_negative_metric` limitation unless the metric
allowlist supplies an exact reviewed formula.

### Composite-Dimension Attribution

```sql
WITH
  toDateTime('<current_start>') AS current_start,
  toDateTime('<current_end>') AS current_end,
  toDateTime('<baseline_start>') AS baseline_start,
  toDateTime('<baseline_end>') AS baseline_end,
  dateDiff('second', current_start, current_end) AS current_duration_seconds,
  dateDiff('second', baseline_start, baseline_end) AS baseline_duration_seconds,
  toFloat64(current_duration_seconds) / nullIf(baseline_duration_seconds, 0) AS baseline_normalization_factor,
  current_by_entity AS (
    SELECT
      request_path_norm,
      bot_class,
      <current_metric_expr> AS current_requests,
      <current_support_expr> AS current_support_raw
    FROM <project>.bot_agg_path_day
    WHERE <time_column> >= current_start
      AND <time_column> < current_end
      AND request_host = '<host>'
    GROUP BY request_path_norm, bot_class
  ),
  baseline_by_entity AS (
    SELECT
      request_path_norm,
      bot_class,
      <baseline_metric_expr> AS baseline_raw_requests,
      <baseline_support_expr> AS baseline_support_raw
    FROM <project>.bot_agg_path_day
    WHERE <time_column> >= baseline_start
      AND <time_column> < baseline_end
      AND request_host = '<host>'
    GROUP BY request_path_norm, bot_class
  ),
  by_entity AS (
    SELECT
      coalesce(c.request_path_norm, b.request_path_norm) AS request_path_norm,
      coalesce(c.bot_class, b.bot_class) AS bot_class,
      coalesce(c.current_requests, 0) AS current_requests,
      coalesce(b.baseline_raw_requests, 0) AS baseline_raw_requests,
      coalesce(b.baseline_raw_requests, 0) * baseline_normalization_factor AS baseline_requests,
      coalesce(c.current_support_raw, 0) AS current_support_raw,
      coalesce(b.baseline_support_raw, 0) AS baseline_support_raw
    FROM current_by_entity AS c
    FULL OUTER JOIN baseline_by_entity AS b USING (request_path_norm, bot_class)
  ),
  scored AS (
    SELECT
      *,
      current_requests - baseline_requests AS absolute_delta,
      abs(current_requests - baseline_requests) AS abs_delta,
      sum(abs(current_requests - baseline_requests)) OVER () AS total_abs_delta
    FROM by_entity
  )
SELECT
  request_path_norm,
  bot_class,
  current_requests,
  baseline_raw_requests,
  baseline_requests,
  current_support_raw,
  baseline_support_raw,
  'duration_normalized_to_current_window' AS baseline_value_semantic,
  baseline_normalization_factor,
  absolute_delta,
  total_abs_delta AS complete_scope_total_abs_delta,
  <pct_change_expr_or_null> AS pct_change,
  if(total_abs_delta = 0, NULL, round(abs_delta / total_abs_delta * 100, 2)) AS contribution_pct
FROM scored
ORDER BY abs_delta DESC, toString(request_path_norm) ASC, toString(bot_class) ASC
LIMIT 50
```

### Lifecycle-Friendly Rows

Do not filter out zero-current or zero-baseline entities before lifecycle
classification. If support-threshold filtering is used, it must be applied only
after complete-scope contribution denominators have already been computed. Use
an outer query over `scored`, or apply thresholding locally after the SQL result
is returned:

```sql
WITH
  by_entity AS (...),
  scored AS (
    SELECT
      *,
      current_requests - baseline_requests AS absolute_delta,
      abs(current_requests - baseline_requests) AS abs_delta,
      sum(abs(current_requests - baseline_requests)) OVER () AS total_abs_delta
    FROM by_entity
  )
SELECT *
FROM scored
WHERE current_support_raw >= <min_count>
   OR baseline_support_raw >= <min_count>
ORDER BY abs_delta DESC
LIMIT 50
```

For trusted `new` and `disappeared` classification, the SQL/template package
must also preserve rows whose support is zero on one side and positive on the
other. A single grouped query over the union of current and baseline windows, or
a full-scope joined current/baseline template, can produce those rows; separate
limited top-N current and baseline queries cannot. The wrapper must attach
`zero_fill_evidence` for the same generated query. The local normalizer should
then map `current_support_raw` and `baseline_support_raw` to lifecycle support,
and may classify:

- `presence_lifecycle: "new"` only when `baseline_support_raw = 0` is backed
  by trusted baseline zero-fill evidence and `current_support_raw >=
  min_count`;
- `presence_lifecycle: "disappeared"` only when `current_support_raw = 0` is
  backed by trusted current zero-fill evidence and `baseline_support_raw >=
  min_count`;
- `presence_lifecycle: "existing"` when both support counts are positive.

Avoid:

```sql
HAVING current_requests > 0
   AND baseline_requests > 0
```

The `AND` form hides new and disappeared entities. Also avoid putting
`HAVING current_requests >= <min_count> OR baseline_requests >= <min_count>`
inside `by_entity` before `total_abs_delta` is computed. Any threshold filter
before denominator computation invalidates complete-scope contribution
evidence.

## Implementation Plan

V1 should ship in three deliverable phases. Each phase must keep the document's
trust model intact rather than exposing later-phase behavior early.

### v1a: Standalone Conservative Normalizer

1. Define the normalizer interface in
   `skills/bot-insights/scripts/attribution.py` as both a file/stdin CLI and a
   library entrypoint such as
   `normalize_attribution(input_doc, trusted_context=None)`. The CLI path must
   always pass `trusted_context=None`.
2. Add `skills/bot-insights/scripts/attribution.py` with CLI options for
   file/stdin input, `--metric`, `--dimensions`, `--min-count`, `--limit`, and
   output mode. Default `--min-count` to `100`.
3. Implement JSON reading compatible with MCP results, saved JSON, pasted JSON,
   wrapped MCP sidecar input, and list-of-dict rows.
4. Enforce the standalone trust cap: file/stdin JSON can emit partial low or
   medium reports, but cannot emit high confidence, trusted contribution,
   trusted zero-fill/lifecycle absence, or `scorecard_export_safe: true`.
   Trusted-looking fields inside JSON are assertions only.
5. Implement row-shape detection, mixed-shape rejection, selected-metric alias
   normalization, requested dimension normalization, baseline method
   validation, `baseline_value_semantic` validation, and
   `baseline_windows_not_reduced` handling.
6. Validate metric semantics through the v1 reviewed allowlist and alias map;
   derive normalized `metric_kind` from that allowlist and treat caller
   `metric_kind` as assertion/debug metadata only.
7. Compute selected-metric `absolute_delta`, guarded `pct_change`, `direction`,
   and ranking when current and baseline metric values are present or safely
   zero-filled. This movement path is independent from lifecycle/support
   classification.
8. Resolve `presence_lifecycle`, `support_change_label`, and sparse lifecycle
   candidate flags only after support fields and period-presence evidence are
   available. Non-volume metrics without support fields may still produce
   ranked mover rows, but lifecycle and support-change labels must be
   `not_evaluated` or omitted according to the output mode.
9. Preserve returned-row totals and buckets separately from any complete-scope
   denominator, apply deterministic ranking with dimension tie-breakers, add
   deterministic low/medium confidence labels, and emit
   `bot_attribution_report.v1`.
10. v1a must not include a scorecard export subcommand, scorecard output mode,
    `bot_scorecard_input.v1` artifact, or any path that marks advanced exports
    as safe.

V1a acceptance criteria: standalone public JSON normalization works
conservatively; no public/file/stdin/pasted/saved JSON path emits
`confidence: "high"`; trusted-looking JSON fields are caller assertions only;
unsafe contribution evidence degrades to `contribution_basis: "none"`; and no
scorecard export command, flag, output mode, `bot_scorecard_input.v1`, or
`scorecard_export_safe: true` exists.

### v1b: Reviewed Generator And Optional Skill-Controlled Runner

1. Add the reviewed `bot-insights-attribution-sql` generator boundary and, if
   the repo can provide reviewed host/plugin integration, the
   `bot-insights-attribution-runner` skill-controlled direct-MCP wrapper
   boundary.
   The generator must validate retained grouped dimensions and retained
   scope/filter columns, resolve reviewed metric aliases, consume trusted
   Hydrolix table metadata, build metadata-derived merge expressions for
   aggregate-state columns, compute `metadata_fingerprint`, bind selected
   table/columns and metadata origin into `query_fingerprint`, and emit
   SQL/templates plus provenance metadata. Checked-in metadata fixtures may be
   used for generator/unit tests and metadata fingerprint tests, but generator
   provenance without the direct-MCP wrapper skill-controlled path remains
   assertion-only.
   The optional direct-MCP wrapper must own the MCP call, direct in-memory result
   receipt, result mapping, digest computation, trusted-context construction,
   and in-process `normalize_attribution(input_doc, trusted_context=...)`
   call. If that wrapper cannot be implemented and tested in v1b, v1b is capped
   at generated SQL/templates plus standalone normalization at medium
   confidence or lower;
   direct-MCP high confidence moves to a later phase. Saved, pasted, or
   user-editable MCP JSON remains capped below high.
2. Validate selected summary-table support for every requested grouped
   dimension and every scope/filter column. Unsupported filter columns must
   produce `unsupported_summary_filter`; unsupported grouped dimensions must
   produce `unsupported_summary_dimension_set`. Either condition prevents high
   confidence unless trusted raw fallback coverage evidence proves equivalent
   coverage, and raw fallback remains medium at best.
3. Validate `complete_rowset_evidence`, including direct-MCP wrapper delivery,
   reviewed generator/source path, metric, grouped dimensions, scope/filter
   columns, windows, baseline method, baseline value semantic, normalization
   factor, grouped scope completeness, no source limit, no
   pre-group/pre-denominator filter outside declared scope, and template/query
   identity before computing local contribution.
4. Validate `complete_scope_pre_limit` denominator identity, including
   promotion of identical per-row MCP denominators plus explicit same-scope,
   no-pre-filter, no-source-limit, trusted provenance, pre-output-limit
   evidence, and matching baseline normalization metadata; degrade invalid
   evidence to `contribution_basis: "none"` rather than failing the whole
   report.
5. Validate `provided_complete_scope` evidence fields against the same metric,
   grouped dimensions, scope/filter columns, windows, baseline method, baseline
   value semantic, and any baseline normalization metadata as the report, and
   validate supplied
   `contribution_pct` against
   `abs(absolute_delta) / complete_scope_total_abs_delta * 100` within the
   configured tolerance.
6. Validate period-split absence trust before zero-filling missing opposite
   periods, and validate combined-row zero trust before using zero values as
   lifecycle absence evidence. Reject duplicate entity/period rows by default;
   aggregate them only when trusted duplicate-aggregation evidence proves
   disjoint partitioning/source grouping semantics. Exclude unsafe one-sided
   rows from ranked movers by default; explicitly emitted one-sided sparse
   candidates use `presence_lifecycle: "not_evaluated"`.
7. Add high-confidence labels only through the skill-controlled direct-MCP
   wrapper path, with trusted evidence payloads from
   `trusted_context.trusted_evidence` and a recomputed `result_digest`. If v1b
   ships without that wrapper, no v1b path emits high confidence. Standalone
   JSON remains capped below high.
8. Add reference documentation for advanced attribution, skill-controlled
   generator interface, SQL window predicates, baseline normalization metadata,
   and metadata-derived merge expressions.

V1b acceptance criteria: reviewed SQL/template generation and provenance are
implemented and tested; the direct-MCP runner is optional; if the runner is not
implemented in this repo, no v1b path emits high confidence, trusted
contribution, or trusted lifecycle absence from MCP results; saved, pasted,
stdin, file, notebook, and public MCP JSON remain capped below high; and v1b
still exposes no scorecard export command, flag, output mode,
`bot_scorecard_input.v1`, or `scorecard_export_safe: true`.

### v1c: Scorecard Hardening And Explicit Export

1. Harden `skills/bot-insights/scripts/scorecard.py` before enabling any
   advanced scorecard export. It must reject direct
   `bot_attribution_report.v1` input, require an internal in-process
   `bot_scorecard_input.v1` object plus `scorecard_export_safe: true`,
   digest-bound `scorecard_handoff_evidence`, and an in-process
   `scorecard_trusted_context` from reviewed export code. The hardened library
   entrypoint should expose `scorecard_trusted_context=None`, with CLI/file/stdin
   callers always passing `None`. It must maintain separate preserve-safe and
   compute-safe contribution gates and reject direct attribution reports plus
   boolean-only, saved, pasted, or caller-supplied `bot_scorecard_input.v1`
   artifacts when that trusted context is absent or invalid.
2. Only after those tests pass, add an explicit scorecard export subcommand
   that constructs `bot_scorecard_input.v1` only as an internal in-process
   handoff, validates it with `scorecard_trusted_context`, invokes hardened
   scorecard generation, and emits the final scorecard artifacts. The internal
   handoff must include top-level `entity_type` and/or concrete supported
   entity fields such as `client_asn`, include `scorecard_handoff_evidence`,
   and omit legacy compute-safe metadata for advanced preserve-only bases.
3. Reject or skip composite scorecard exports in v1 with
   `composite_scorecard_export_not_supported`.
4. v1c acceptance criteria include: no direct `bot_attribution_report.v1`
   input accepted by `scorecard.py`; no advanced export marked safe without
   an internal in-process `bot_scorecard_input.v1`, digest-bound
   `scorecard_handoff_evidence`, and in-process `scorecard_trusted_context`;
   schema version plus
   `scorecard_export_safe: true` alone is rejected; preserve-safe contribution
   never reconstructs missing percentages; compute-safe contribution is
   allowed only for trusted complete rowsets; `scorecard.py` has separate
   preserve-safe and compute-safe gates; and no advanced scorecard export
   command, flag, alternate output mode, or scorecard-safe artifact exists in
   v1a or v1b.

Cross-phase documentation tasks: update `skills/bot-insights/SKILL.md` so
legacy simple movers still route to `compare_posture.py`; update relevant Bot
Insights references, including baseline comparison, summary-table, and
scorecard-analysis docs; and add migration notes showing when to keep using
`bot_mover_attribution.v1` versus `bot_attribution_report.v1`.

## Test Coverage Recommendations

Unit tests should cover:

Organize coverage by delivery phase:

- v1a tests cover standalone normalization, low/medium confidence caps,
  baseline value semantics, row-shape handling, mover ranking, lifecycle
  `not_evaluated` behavior, and the absence of any scorecard export command.
- v1b tests cover trusted generator provenance, metadata-derived merge
  expressions, summary grouped-dimension and scope/filter validation, raw
  fallback coverage, zero-fill evidence validation, and complete-scope
  contribution validation. If the direct-MCP wrapper is implemented in v1b,
  tests must also cover wrapper-owned MCP result mapping, digest computation,
  trusted wrapper invocation, and high-confidence evidence. If the wrapper is
  not implemented, tests must prove v1b remains capped below high.
- v1c tests cover `scorecard.py` hardening and the explicit scorecard export
  path. No v1a or v1b test should depend on advanced scorecard export existing.

- MCP `columns`/`rows` conversion.
- Caller-editable MCP wrapper JSON with `metadata` sidecar and `mcp_result`
  remains assertion-only unless a reviewed direct-MCP wrapper supplies the same
  facts through in-memory `trusted_context`.
- Plain MCP `columns`/`rows` accepted as metadata-poor input with low
  confidence and no trusted contribution or lifecycle absence.
- Plain MCP `columns`/`rows` with explicit top-level `dimensions` uses those
  dimensions after conflict validation.
- Plain MCP `columns`/`rows` without top-level `dimensions` may use CLI
  `--dimensions` or deterministic inferred scalar columns, preserves MCP column
  order, echoes inferred dimensions, adds `dimensions_inferred`, and remains
  capped below high.
- Plain MCP `columns`/`rows` with no inferable dimensions or ambiguous
  metric/dimension inference fails with `no_inferable_dimensions` or
  `dimension_inference_ambiguous`, and ambiguous metric inference fails with
  `ambiguous_metric_input`.
- Standalone `attribution.py` file/stdin input containing
  `evidence_source: "trusted_template_generator"` remains caller-asserted,
  caps confidence below high, and emits no trusted contribution or trusted
  lifecycle absence.
- Trusted wrapper invocation passes an in-memory `trusted_context` and may
  unlock trusted contribution and lifecycle absence only for evidence payloads
  present as typed list entries in `trusted_context.trusted_evidence`, with
  unique `evidence_id`, recognized `evidence_type`, matching `applies_to`,
  matching `query_fingerprint`, and recomputed `result_digest`.
- If implemented in v1b, the MCP high-confidence path receives the direct MCP
  result object through a reviewed wrapper; the same MCP `columns`/`rows` saved
  to disk or pasted into stdin remains metadata-poor and capped below high.
- Trusted-looking evidence present only in `input_doc` is ignored for
  trust-unlocking decisions and preserved, if at all, only under
  `input_assertions` or debug output.
- Arbitrary in-process construction of `trusted_context` is documented as out
  of scope for v1 public-input hardening; tests verify the accident-prevention
  boundary for CLI/file/stdin/user-editable JSON paths.
- Missing, malformed, or caller-editable trusted context emits
  `trusted_context_missing` or `trusted_context_invalid` and caps confidence
  below high.
- Missing `query_fingerprint` emits `query_fingerprint_missing`; missing
  `result_digest` emits `result_digest_missing`.
- A recomputed result digest mismatch emits
  `trusted_context_digest_mismatch` and downgrades all digest-bound trusted
  evidence.
- `digest_payload_v1` canonicalization covers field inclusion, row ordering,
  null versus missing, timestamp normalization, decimal formatting,
  digest-canonical precision versus public display precision, finite number
  rejection, and lowercase `sha256:<hex>` formatting.
- Public output examples may trim display decimals while the recomputed digest
  payload uses exact six-decimal value-class and exact two-decimal
  percentage-class canonical values.
- Non-finite JSON numbers (`NaN`, `Infinity`, `-Infinity`), negative support
  counts, negative contribution denominators, and out-of-range
  `contribution_pct` values are rejected for core fields.
- Rounding occurs before contribution consistency checks and before digest
  calculation.
- Negative-valued metric test inputs omit or null `pct_change` with
  `pct_change_not_defined_for_negative_metric` unless the allowlist provides a
  reviewed metric-specific formula.
- Missing or mismatched trusted evidence payloads emit
  `trusted_evidence_missing` or `trusted_evidence_mismatch`.
- Trusted evidence must include matching `metadata_fingerprint`, selected
  table, selected columns, metadata origin, production metadata retrieval
  identity, and merge expressions; mismatches cap confidence below high.
  Fixture identity belongs in generator/unit tests and does not unlock high
  confidence for standalone JSON.
- Metadata conflicts across CLI flags, top-level fields, metadata sidecars,
  row fields, MCP columns, and trusted context are deterministic: core-field
  conflicts are fatal or downgrade trust with `metadata_conflict`.
- Trusted generator rejects unsupported retained dimension sets before emitting
  SQL.
- Trusted generator rejects or downgrades unsupported summary scope/filter
  columns with `unsupported_summary_filter`, even when the grouped dimensions
  are retained.
- Trusted generator uses Hydrolix metadata-derived merge expressions for
  aggregate-state columns instead of plain `sum(...)`.
- MCP duplicate column names, blank column names, and row-length mismatches are
  fatal.
- Fatal invalid inputs emit `bot_attribution_error.v1` or equivalent typed
  library errors for malformed JSON, mixed row shapes, missing requested
  dimensions, no usable metric values, duplicate entity/period keys without
  trusted aggregation evidence, duplicate or blank MCP columns, MCP row-length
  mismatches, and unmappable MCP rows.
- Combined rows.
- Period-split rows.
- Period-split missing opposite row zero-filled only with trusted
  `zero_fill_evidence.period_value_trust.<side>: "complete_grouped_scope"`
  evidence.
- Period-split missing opposite row zero-filled only with trusted
  `zero_fill_evidence.period_value_trust.<side>: "trusted_full_scope_join"`
  evidence.
- Combined-row `current_*: 0` or `baseline_*: 0` does not prove `new`,
  `disappeared`, or sparse lifecycle evidence without trusted
  `zero_fill_evidence.period_value_trust`.
- Separate limited top-N current/baseline rows do not become `new` or
  `disappeared`.
- Separate limited top-N one-sided rows are excluded from ranked movers, totals,
  and buckets by default with `period_absence_not_trusted`.
- Skipped unsafe rows use bounded `not_evaluated_components` with
  `skipped_count` and limited `sample_entity_values`, not unbounded entity
  dumps.
- Period-split duplicate entity/period rows are rejected by default, including
  for additive metrics.
- Period-split duplicate aggregation succeeds only with trusted
  `duplicate_aggregation_evidence` proving disjoint partition/source grouping
  semantics for the same report contract and digest.
- Multiple-baseline input is accepted only when each entity has one
  pre-reduced normalized baseline value plus explicit `baseline_method`.
- `baseline_value_semantic` is required for high confidence.
- Raw total-window month-over-month input with unequal durations emits
  `calendar_length_difference_not_normalized` unless the report explicitly
  declares that raw total-window comparison is intended.
- Duration-normalized baseline input requires trusted `baseline_normalization`
  metadata with method, durations, factor, formula, and applied fields.
- Normalized `baseline_normalization.applies_to` uses `["baseline"]`; input
  aliases such as `baseline_requests` map to output `baseline` or remain
  untrusted assertions if ambiguous.
- Duration-normalized baseline input applies normalization to baseline metric
  fields, not raw support threshold fields.
- Lifecycle thresholds, sparse lifecycle flags, and high-confidence lifecycle
  support checks use `current_support_raw` and `baseline_support_raw`; optional
  `baseline_support_normalized` is display/comparison only.
- Trusted generator SQL uses explicit current/baseline window predicates and
  does not scan the gap between non-adjacent baseline and current windows.
- Trusted generator SQL uses period-scoped current/baseline metric and support
  expressions, for example separate `current_by_entity` and
  `baseline_by_entity` CTEs, and rejects templates that reuse the same
  unfiltered aggregate over a combined current-plus-baseline rowset for both
  periods.
- Period-split rows split by multiple baseline windows are rejected or skipped
  with `baseline_windows_not_reduced`; v1 does not locally compute
  `mean_of_baseline_windows` or
  `duration_weighted_mean_of_baseline_windows`.
- Mixed row shape rejection.
- Missing dimension handling.
- Missing metric handling.
- Selected-metric aliasing for `current_<metric>`, `<metric>_current`,
  `baseline_<metric>`, and `<metric>_baseline`.
- Caller-supplied `metric_kind: "additive_count"` does not enable contribution
  math for a metric absent from the reviewed additive allowlist.
- Normalized `metric_kind` is derived from the reviewed metric allowlist;
  caller `metric_kind` is optional assertion/debug metadata and is not required
  for a useful report.
- Single-dimension attribution.
- Composite-dimension attribution.
- Composite scorecard export rejected or skipped with
  `composite_scorecard_export_not_supported`.
- Supported summary dimension-set validation.
- Unsupported summary dimension-set limitation.
- Unsupported summary filter limitation.
- New entity `presence_lifecycle`.
- Disappeared entity `presence_lifecycle`.
- Existing entity with equal positive support emits
  `presence_lifecycle: "existing"` and
  `support_change_label: "support_unchanged"`.
- Zero/zero trusted support is bucket-only in v1: it may increment
  `absent_count` and `support_zero_both_count` when bucket evidence is
  complete, but it is not emitted as a ranked mover with
  `presence_lifecycle: "absent"`.
- Additive count metrics use selected metric values as support only when the
  metric represents entity volume.
- Rate, average, percentile, approximate unique, score, and ratio movers
  require `current_support_raw` and `baseline_support_raw` for lifecycle.
- Non-volume mover without support fields emits
  `presence_lifecycle: "not_evaluated"` and
  `support_change_label: "not_evaluated"` with
  `lifecycle_support_missing`, while movement can still be ranked when both
  metric values are present.
- Sparse new candidate flag emits `presence_lifecycle: "not_evaluated"`
  without asserted `new` lifecycle.
- Sparse disappeared candidate flag emits
  `presence_lifecycle: "not_evaluated"` without asserted `disappeared`
  lifecycle.
- Ramp-up from sparse nonzero baseline classified as `existing` with
  `sparse_baseline_support`, not `new`.
- Ramp-down to sparse nonzero current classified as `existing` with
  `sparse_current_support`, not `disappeared`.
- Fractional positive baseline such as `0.5` is sparse existing support, not
  `new`, unless separate trusted absence evidence exists.
- Exact zero baseline guarded percentage change emits `zero_baseline_guard`.
- Fractional positive baseline guarded percentage change emits
  `subunit_baseline_guard`, not `zero_baseline_guard`.
- High aggregate confidence for sufficiently supported trusted `new` entity
  with valid direct-MCP `trusted_context`, while percentage-change
  interpretation remains guarded.
- High aggregate confidence for trusted `disappeared` entity requires
  sufficient baseline support plus trusted current absence or zero and valid
  direct-MCP `trusted_context`.
- Existing high confidence requires positive current and baseline support that
  both meet threshold plus valid direct-MCP `trusted_context`; sparse existing
  support is not high-confidence lifecycle support evidence.
- Sparse count confidence downgrade.
- Complete-scope contribution computation only for `complete_rowset`.
- `complete_rowset` computes contribution only when
  `complete_rowset_evidence` includes direct-MCP wrapper delivery, reviewed
  generator/source path, metric, dimensions, scope, windows, baseline method,
  grouped scope completeness, no source limit, no pre-group/pre-denominator
  filter outside declared scope, and template/query identity.
- Complete-scope pre-limit contribution preservation requiring both
  `complete_scope_total_abs_delta` or promotable identical per-row denominator
  and per-row `contribution_pct`, plus same-scope, no-source-limit,
  no-pre-filter, trusted provenance, and `computed_before_output_limit: true`
  evidence.
- Complete-scope pre-limit missing contribution is not reconstructed from
  returned rows.
- Caller-asserted complete-scope metadata is preserved only as debug assertion,
  caps confidence below high, and does not emit trusted `contribution_pct`.
- Invalid `complete_scope_pre_limit` evidence degrades to partial output with
  `contribution_basis: "none"` and no trusted `contribution_pct`.
- MCP per-row denominator promotion succeeds only when denominators are
  identical and match any top-level denominator.
- Identical per-row denominators alone do not prove full-scope coverage.
- MCP per-row denominator mismatch invalidates contribution evidence.
- Returned-row `total_current`, `total_baseline`, `total_delta`,
  `total_abs_delta`, and `buckets` when output is limited.
- Contribution withheld when scope is incomplete.
- Contribution withheld for non-additive metrics.
- Provided contribution preserved only when required
  `provided_contribution_evidence` fields are present, trusted, and valid.
- Provided contribution evidence fails when metric, dimensions, scope, windows,
  baseline method, baseline value semantic, baseline normalization metadata,
  source-limit, pre-filter, or output-limit evidence does not match the report.
- Supplied `contribution_pct` is discarded with
  `provided_contribution_inconsistent` when it differs from
  `abs(absolute_delta) / complete_scope_total_abs_delta * 100` by more than the
  configured tolerance.
- Zero complete-scope denominator emits null or omitted `contribution_pct`.
- Missing contribution for preserve-only bases is not reconstructed even when a
  denominator is present.
- Invalid `provided_complete_scope` evidence degrades to partial output with
  `contribution_basis: "none"` and no trusted `contribution_pct`.
- Source-limit and output-limit limitations.
- Support-threshold filtering after `scored` preserves complete-scope
  denominator evidence; threshold filtering before denominator computation
  invalidates it.
- Scorecard handoff preserves provided complete-scope contribution without
  making missing contribution locally compute-safe.
- v1a and v1b expose no advanced scorecard export subcommand, flag, output
  mode, `bot_scorecard_input.v1` artifact, or scorecard-safe artifact.
- Scorecard handoff for `complete_scope_pre_limit` and
  `provided_complete_scope` omits legacy compute-safe metadata unless
  `scorecard.py` has separate preserve and compute gates.
- `scorecard.py` rejects direct `bot_attribution_report.v1` input even when the
  report contains `rowset_complete: true` or contribution metadata.
- `scorecard.py` rejects advanced handoff unless the in-process object is
  `bot_scorecard_input.v1` with `scorecard_export_safe: true`, digest-bound
  `scorecard_handoff_evidence`, and in-process `scorecard_trusted_context`
  from reviewed export code.
- `scorecard.py` rejects boolean-only `bot_scorecard_input.v1` input where
  schema version plus `scorecard_export_safe: true` is present but validated
  handoff evidence or in-process scorecard context is missing.
- Missing contribution reconstruction succeeds only for compute-safe
  `complete_rowset` exports with trusted complete-rowset evidence.
- Missing contribution reconstruction is rejected for preserve-only
  `complete_scope_pre_limit` and `provided_complete_scope` exports.
- Scorecard export is disabled when trusted per-row `contribution_pct` is
  absent for preserve-only advanced bases.
- Scorecard export is an explicit in-process handoff that emits final
  scorecard artifacts only after validation.
- `scorecard.py` always rejects direct `bot_attribution_report.v1` input.
  Reviewed export code may separately construct an in-memory
  `bot_scorecard_input.v1` handoff and pass it with
  `scorecard_trusted_context`.
- Raw fallback confidence downgrade.
- Raw fallback remains low without explicit source coverage evidence, may
  become medium with evidence, and is never high in v1.
- Raw fallback evidence must include source table, filters, scope, windows,
  dimensions, metric semantics, coverage caveats, and same-population evidence.
- Deterministic confidence caps for metadata-poor input, caller assertions,
  invalid contribution consistency, and the direct-MCP wrapper high-confidence
  happy path.
- Deterministic ranking tie-breakers by dimension values.
- Summary-table high-confidence happy path.

## Risks And Edge Cases

- Existing `bot_mover_attribution.v1` computes contribution over returned rows
  when no total is provided. The advanced engine should be stricter without
  changing that legacy behavior unexpectedly.
- The local script must only consume aggregate JSON, MCP output, or pasted JSON.
  It must not query Hydrolix, read credentials, or process raw large rowsets.
- Summary tables do not retain every desirable dimension. The engine must
  expose missing retained dimensions instead of silently implying coverage.
- Composite dimension cardinality can be large. Hydrolix should aggregate,
  compute complete-scope contribution denominators, and rank before local
  scripts receive rows.
- Returned-row totals are intentionally not complete-scope totals when output
  limiting is applied. Consumers must use `complete_scope_total_abs_delta` only
  for contribution interpretation after trusted provenance and algebraic
  consistency checks pass, not as a replacement for returned-row totals.
- Caller-supplied complete-scope booleans are self-attesting unless generated
  by a reviewed template path and delivered through the direct-MCP wrapper
  skill-controlled boundary. Treating standalone JSON fields as trusted
  evidence would recreate the limited-rowset confidence bug in a different
  field shape.
- Scorecard handoff depends on separating preserve-safe contribution evidence
  from compute-safe contribution evidence. Updating `scorecard.py` to treat all
  advanced `contribution_basis` values as local-compute safe would recreate the
  limited-rowset bug this design is trying to avoid. Until that separation
  exists, direct attribution-report input must be rejected, and
  advanced exports must avoid legacy compute-safe metadata or be disabled for
  preserve-only bases.
- Composite attribution reports are valid v1 output, but composite rows are not
  first-class scorecard input in v1.
- Request-path attribution is summary-backed today for `request_path_norm`,
  `request_path_norm + bot_class`, and `request_path_norm + asn_type`, but not
  for `request_path_norm + client_asn` or `request_path_norm + ai_category`.
- ASN-plus-path summary support would require a catalog/schema expansion. It is
  cardinality-risky rather than automatically too high-cardinality; validate
  observed grouped row counts before adding it.
- New and disappeared labels are sensitive to sparse support, retention gaps,
  normalized fractional baselines, and period-split absence evidence. Separate
  limited top-N period queries cannot prove absence from the opposite period,
  and fractional positive support is not zero.
- Zero-baseline percentage changes are mathematically useful but easy to
  overstate.
- Raw fallback aggregates can be valid but do not prove summary-table coverage.
  They are low confidence by default, medium only with explicit source coverage
  evidence, and never high in v1.
- Displacement candidates are deferred to v2 because they can sound causal and
  need a deterministic output contract before implementation.
- SIEM and non-SIEM summaries may have different source coverage.
- Aggregate-state columns require Hydrolix metadata and merge functions in SQL,
  not script-side table assumptions.

## Open Questions

- Should v2 support multiple metrics behind a still-simple schema?
- Should limited-scope contribution remain omitted entirely, or be emitted in v2
  under a clearly named field such as `returned_rows_contribution_pct`?
- Should v2 add first-class composite scorecard rows with `entity_type:
  "composite"`, deterministic `entity_key`, and structured `entity_values`?
- Should v2 add metric-specific support thresholds beyond the v1 default
  `min_count: 100`?
- Should v2 add signed portable evidence or process isolation so saved JSON can
  carry origin guarantees stronger than the v1 workflow boundary?
- Should v2 add an explicit non-ranked zero/zero row mode where `absent` and
  `support_zero_both` can appear as emitted diagnostic row labels?
- Should `bot_mover_attribution.v1` eventually be marked deprecated, or kept as
  a permanently supported simple schema?
