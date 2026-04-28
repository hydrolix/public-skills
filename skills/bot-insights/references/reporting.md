# bot-insights - Reporting

[scripts/render_report.py](../scripts/render_report.py) renders existing Bot
Insights artifacts as Markdown or a self-contained HTML page with inline SVG
charts. It is a deterministic view layer on top of artifact JSON that has
already been produced by `compare_posture.py`, `scorecard.py`, or another
upstream step. The renderer does not query Hydrolix, open database clients,
read credentials, recompute scores, or infer values beyond the fields already
present in the input.

## Accepted Input

The renderer accepts exactly three top-level JSON shapes:

- A single known artifact object with a supported `schema_version`.
- A non-empty array of known artifact objects. `--report-type` is required
  for raw arrays because the input carries no durable report intent.
- A `bot_report_input.v1` wrapper object. The wrapper is the preferred form
  for reusable demos because it carries report intent, stable artifact IDs,
  a presentation scope label, and analyst-note citations.

Supported artifact schemas:

- `bot_posture_movement.v1`
- `bot_mover_attribution.v1`
- `bot_control_review.v1`
- `bot_scorecard_index.v1`
- `bot_entity_scorecard.v1`
- `bot_scorecard_artifacts.v1` (decomposed into a nested index and
  scorecards during normalization)

Unknown schemas are rejected unless `--allow-unknown` is set, and even then
they are only reported as skipped input. `--allow-unknown` does not make an
unknown artifact eligible to satisfy a required-artifact rule.

Wrapper `report_type`, when present, must be a string matching a supported
report type. Explicit artifact IDs must be non-empty strings and must not use
reserved generated-child suffixes such as `#index` or `#scorecard-N`.
Analyst-note `json_pointer` values are resolved strictly as RFC 6901 pointers;
array tokens must be non-negative indexes without leading zeroes.

## Supported Report Types

- `executive_posture` - posture movement plus optional scorecard ranking and
  movers.
- `soc_triage` - prioritized risky entities, scorecard ranking, per-entity
  scorecard analysis, evaluated feature evidence, recommended next steps, and
  missing-evidence limits when scorecards are supplied; renders a ranking-only
  degraded report when only a `bot_scorecard_index.v1` is available.
- `control_review` - before/after/expected effectiveness review.
- `scorecard_brief` - single-entity brief with domain scores, feature
  evidence, and recommended next steps from the artifact.
- `crawler_governance` - crawler and AI-crawler posture using only evaluated
  `crawler_governance` scorecard features.
- `edge_ops_impact` - cache-busting and origin-impact evidence using only
  evaluated `cache_busting` and `origin_impact` scorecard features.

## Commands

Markdown to stdout:

```bash
uv run python skills/bot-insights/scripts/render_report.py \
    --file skills/bot-insights/examples/executive-posture.json
```

Self-contained HTML to a file:

```bash
uv run python skills/bot-insights/scripts/render_report.py \
    --file skills/bot-insights/examples/soc-triage.json \
    --format html \
    --output /tmp/soc-triage.html
```

Raw single artifact needs `--report-type`:

```bash
cat control_review.json \
  | uv run python skills/bot-insights/scripts/render_report.py \
      --report-type control_review
```

Raw artifact arrays always require `--report-type`.

## Warnings and Evidence Limits

The renderer emits warnings to stderr and to a Warnings section in the
rendered report when:

- CLI `--title` or `--limit` overrides a wrapper value.
- Optional companion artifacts are dropped for cross-artifact compatibility
  reasons.
- A SOC report degrades to ranking-only because no compatible scorecards
  were supplied.
- A domain report has scorecards but none contain relevant evaluated
  features.
- An analyst note has no cited data sources.
- Display limits truncate rendered rows.

Warnings are visible diagnostics. They do not hide required artifacts or
convert missing evidence into safe-looking output. Missing feature inputs
remain in `not_evaluated_features` and are listed as evidence limits rather
than ignored.

## Artifact-Only Boundary

The renderer is intentionally thin:

- It never queries Hydrolix.
- It never recomputes scores, deltas, contribution percentages, or
  confidence.
- It never invents analyst commentary or follow-up steps. Analyst-authored
  narrative must be supplied through `analyst_notes` and is labeled as
  interpretation.
- It never uses analyst notes as input for metric values, chart values,
  ranks, report selection, duplicate detection, or row-limit calculations.

Use the Hydrolix MCP server or host Hydrolix query tool to produce the
aggregate rows. Run `compare_posture.py` or `scorecard.py` to emit artifact
JSON. Only then does `render_report.py` consume those saved artifacts.

## Examples

Four runnable demo inputs live in [../examples/](../examples/):

- [executive-posture.json](../examples/executive-posture.json) - week-over-week
  posture with ASN mover attribution and an analyst note.
- [soc-triage.json](../examples/soc-triage.json) - scorecard packet that
  normalizes into an index and compatible scorecards, with a citation into
  both child artifacts.
- [control-review.json](../examples/control-review.json) - before/after/expected
  review for a simulated bot-blocking policy.
- [crawler-governance.json](../examples/crawler-governance.json) - crawler
  scorecard packet with governance features and AI-crawler growth.

Each example is a complete `bot_report_input.v1` wrapper. Run any of them
with the command above to produce a Markdown or HTML report without any
Hydrolix access.
