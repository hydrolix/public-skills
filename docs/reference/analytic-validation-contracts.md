# Analytic Validation Contracts

This contract defines reusable validation expectations for Hydrolix-backed
analytic skills. It applies to skills such as Bot Insights, CDN Insights, API
Insights, and future analytic skills that discover source data, aggregate it,
generate artifacts, and render reports.

The contract is intentionally generic. Skill-specific bundles, scorecards,
queries, and report layouts may add stricter requirements, but they should not
weaken these phases.

## Required Phases

### 1. Source Routing

Validation must prove that each requested analysis route uses the intended
cluster, database, table family, time window, and renderer/report type.

Checks should verify:

- The active cluster and explicit `--cluster` value agree when both are
  available.
- The database and table names come from discovered metadata, skill references,
  or user-provided inputs, not from an unrelated skill or bundle.
- Summary tables are preferred for aggregate report sections when their
  retained dimensions cover the requested lens.
- Request-level tables are used only when required fields are not retained in a
  suitable summary table.
- Generated artifacts record the source route they used.

### 2. Source Discovery And Metadata Inspection

Before classifying gaps or generating reports, validation must inspect the
available source surface.

Checks should verify:

- The candidate tables exist in the requested database.
- Required columns for each analytic lens exist with compatible types.
- Retained dimensions and aggregate metrics are documented for summary tables.
- Missing optional fields are recorded without failing unrelated lenses.
- Any fallback from summary to request-level data is recorded with the reason.

### 3. Input Coverage

Validation must separate absent input data from absent implementation.

Checks should verify:

- Each lens declares required, optional, and context-required inputs.
- Each required input is populated or classified with a gap reason.
- Optional inputs do not suppress report generation when absent.
- Context-required inputs are requested from the user or marked
  `context_required`; they must not be inferred from unrelated fields.
- Expected zero values are represented as valid findings, not missing features.

### 4. Artifact Generation

Analytic runners must emit machine-readable artifacts that support independent
review of the rendered report.

Checks should verify:

- Every generated report has a corresponding structured artifact.
- Artifacts include source route, lens name, time window, query or method name,
  row counts, gap classifications, warnings, and caveats.
- Aggregate summaries preserve enough raw input to reproduce report totals and
  top lists.
- Report text is generated from artifacts rather than from direct, hidden
  database reads.

### 5. Rendered Report Smoke Validation

Validation must exercise the full path from source discovery through rendered
markdown.

Checks should verify:

- The rendered report exists and is non-empty.
- Each requested lens has a rendered section or an explicit gap classification.
- Required caveats are visible when their triggering conditions are present.
- Aggregate-summary sections use aggregate inputs and include row-count or
  coverage evidence.
- No section claims a feature is unavailable when the input state is better
  classified as sparse, null, context-required, expected zero, or outside the
  requested lens.

See [Analytic Smoke Tests](../workflows/analytic-smoke-tests.md) for the
recommended runner convention.

### 6. Gap Classification

Every missing, sparse, unsupported, or intentionally empty analytic surface must
use a stable classification. Free-text explanations may add context, but the
structured value should come from this vocabulary.

| Classification | Meaning | Typical Action |
|----------------|---------|----------------|
| `routing_gap` | The analysis looked at the wrong cluster, database, table, time window, source family, or renderer route. | Fix routing or source selection before interpreting results. |
| `schema_gap` | The requested source exists, but required columns, retained dimensions, or compatible types are absent. | Update lens support, choose another source, or document unsupported schema. |
| `row_sparse_or_null` | The source and schema are valid, but rows are absent, sparse, or populated mostly with null values for the requested input. | Render caveats and avoid treating sparse data as missing implementation. |
| `context_required` | The lens needs caller or business context that cannot be derived safely from source data. | Ask for the context or render a blocked/partial lens state. |
| `not_requested_lens` | The input could support a lens that was not requested for the current report. | Do not count this as a missing feature in the requested report. |
| `expected_zero` | A zero value is valid and expected for the lens, population, or time window. | Render the zero with context instead of reporting a gap. |

## Contract Output

Each validation run should produce a summary that includes:

- Skill name and version or commit.
- Cluster, database, time window, and renderer/report type.
- Requested lenses.
- Source tables inspected.
- Artifact paths.
- Rendered report paths.
- Gap classification counts.
- Warnings and caveats.
- Validation status: `passed`, `passed_with_warnings`, or `failed`.

## Related Templates

Use [Analytic Lens Template](../templates/analytic-lens.md) to define the lens
inputs and validation checks that this contract expects.
