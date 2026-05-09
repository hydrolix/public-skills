# Analytic Smoke Tests

This workflow defines a reusable smoke-test convention for Hydrolix-backed
analytic skills. It validates the complete path from source routing and
metadata discovery through artifact generation and rendered markdown reports.

Smoke tests are not a replacement for skill-specific unit tests or query
validation. They exist to catch integration failures that only appear when a
real skill runner, source route, artifact writer, and renderer are exercised
together.

## Runner Convention

Each analytic skill should provide a smoke runner with this command shape:

`scripts/<skill>_smoke.py --cluster --database --window --output-dir`

Where:

- `<skill>` is the skill identifier, such as `bot_insights`, `cdn_insights`, or
  `api_insights`.
- `--cluster` selects the Hydrolix cluster or configured cluster alias.
- `--database` selects the database to inspect.
- `--window` selects the analysis interval. Prefer an explicit ISO-8601 range
  or a documented relative range accepted by the runner.
- `--output-dir` selects a fresh directory for all smoke outputs.

For this repository, invoke Python with `uv run python` when running local smoke
commands.

## Required Outputs

Every smoke run must write these outputs under `--output-dir`:

| Output | Requirement |
|--------|-------------|
| `summary.json` | Machine-readable run status, source route, requested lenses, inspected tables, artifact paths, report paths, warnings, caveats, and gap classification counts. |
| Raw aggregate inputs | Query outputs or serialized input tables used for aggregate report sections. These should be sufficient to reproduce totals and top lists. |
| Generated artifacts | Structured per-lens artifacts used by the renderer. |
| Rendered markdown reports | Final markdown report files produced from the artifacts. |
| Warnings/gap classification summary | Stable classifications for missing, sparse, unsupported, context-required, outside-scope, or expected-zero states. This may be included in `summary.json` and optionally rendered as markdown. |

## Required Validation

A smoke runner should fail only when the skill cannot complete the requested
workflow or violates the generic validation contract. It may pass with warnings
when source data is sparse, optional inputs are absent, or context is required.

Required checks:

- Confirm source routing for cluster, database, time window, table family, and
  renderer/report type.
- Discover candidate tables and inspect required schema before running report
  queries.
- Validate requested lens coverage against each lens definition.
- Prefer summary tables for aggregate report sections when retained dimensions
  fit the lens.
- Preserve raw aggregate inputs for sections that render totals, rates, trends,
  or top lists.
- Generate structured artifacts before rendering markdown.
- Render at least one markdown report or classify every requested lens with a
  stable gap reason.
- Ensure `summary.json` includes warning and gap classification counts.

## Gap Classification Vocabulary

Use these structured classifications in smoke outputs:

- `routing_gap`
- `schema_gap`
- `row_sparse_or_null`
- `context_required`
- `not_requested_lens`
- `expected_zero`

Definitions are maintained in
[Analytic Validation Contracts](../reference/analytic-validation-contracts.md).

## Suggested `summary.json` Fields

The exact schema may evolve per skill, but the top-level summary should include
these fields:

- `status`: `passed`, `passed_with_warnings`, or `failed`.
- `skill`: skill identifier.
- `cluster`: selected cluster or alias.
- `database`: selected database.
- `window`: requested analysis window.
- `report_type`: renderer/report type.
- `requested_lenses`: requested analytic lenses.
- `inspected_tables`: source tables inspected during discovery.
- `raw_inputs`: paths to raw aggregate input files.
- `artifacts`: paths to generated structured artifacts.
- `reports`: paths to rendered markdown reports.
- `warnings`: warning objects or messages.
- `gap_classification_counts`: counts by stable gap classification.

## Review Checklist

Before accepting a new or updated smoke runner:

- Run it against a known populated source window.
- Run it against a sparse or intentionally empty window.
- Inspect `summary.json` for stable routing, artifact, warning, and gap fields.
- Inspect raw aggregate inputs for sections that summarize totals or top lists.
- Inspect rendered markdown for lens sections, caveats, and gap wording.
- Confirm no report labels sparse, null, context-required, outside-scope, or
  expected-zero states as missing implementation.

This workflow implements the validation phases in
[Analytic Validation Contracts](../reference/analytic-validation-contracts.md)
and should be used with the
[Analytic Lens Template](../templates/analytic-lens.md).
