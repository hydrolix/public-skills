# Analytic Lens Template

Use this template when adding or reviewing an analytic lens for a
Hydrolix-backed skill. A lens is a reusable analytical perspective with its own
source population, retained dimensions, inputs, caveats, renderer, and
validation checks.

This template is generic. Keep bundle-specific details in the skill or bundle
that owns them.

## Lens Definition

| Field | Value |
|-------|-------|
| Lens name |  |
| Decision/user persona |  |
| Entity population |  |
| Source tables |  |
| Retained dimensions |  |
| Required inputs |  |
| Optional inputs |  |
| Expected zero values |  |
| Expected caveats |  |
| Context-required inputs |  |
| Renderer/report type |  |
| Validation checks |  |

## Field Guidance

### Lens Name

Use a stable, skill-local identifier. Prefer a name that describes the decision
surface, not the current implementation function.

### Decision/User Persona

State who uses the lens and what decision it supports, such as operations
triage, SEO governance, API owner review, or executive posture reporting.

### Entity Population

Define the rows or entities the lens is allowed to describe. Be explicit when
different lenses use different populations, such as all requests, bot requests,
verified crawler requests, API routes, hosts, users, customers, or origin
requests.

### Source Tables

List the summary and request-level tables the lens may use. If a summary table
is preferred, name the fallback conditions that allow request-level data.

### Retained Dimensions

List dimensions retained in the chosen aggregate source. Do not assume a
dimension is available in a summary table just because it exists in raw request
records.

### Required Inputs

List inputs that must exist and be populated for the lens to render its primary
finding.

### Optional Inputs

List inputs that enrich the lens but must not block rendering when absent.

### Expected Zero Values

List zero values that are meaningful and valid for this lens. Examples include
zero errors, zero attacks, zero cache misses, or zero traffic for a requested
slice.

### Expected Caveats

List caveats that should render when specific conditions are present, such as
low row counts, partial source coverage, mixed populations, missing optional
fields, or summary fallback.

### Context-Required Inputs

List inputs that require user, business, or environment context. These must be
classified as `context_required` when absent rather than inferred from
unrelated source fields.

### Renderer/Report Type

Name the report surface that consumes the lens. Examples include scorecard,
posture report, incident review, customer summary, operational drilldown, or
markdown appendix.

### Validation Checks

List the checks that prove the lens can be rendered correctly. Include source
routing, schema coverage, row coverage, aggregate-summary use, artifact
presence, report section presence, and gap classification expectations.

Use the vocabulary in
[Analytic Validation Contracts](../reference/analytic-validation-contracts.md)
for all missing, sparse, unsupported, or intentionally empty states.
