# bot-insights - Reporting

[scripts/render_report.py](../scripts/render_report.py) renders existing Bot
Insights artifacts as Markdown or a self-contained HTML page with inline SVG
charts. It is a deterministic view layer on top of artifact JSON that has
already been produced by `compare_posture.py`, `scorecard.py`, or another
upstream step. The renderer does not query Hydrolix, open database clients,
read credentials, recompute scores, or infer values beyond the fields already
present in the input.

## Contents

- [Accepted Input](#accepted-input)
- [Supported Report Types](#supported-report-types)
- [Report Workflow Matrix](#report-workflow-matrix)
- [Query Execution Boundary](#query-execution-boundary)
- [Commands](#commands)
- [Skill-Orchestrated LLM Reports](#skill-orchestrated-llm-reports)
- [Warnings and Evidence Limits](#warnings-and-evidence-limits)
- [Artifact-Only Boundary](#artifact-only-boundary)
- [Examples](#examples)

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
- `bot_timeseries.v1` (optional companion evidence where supported)

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

## Report Workflow Matrix

Every final-report workflow follows the same contract:

`bot_report_evidence.v1` evidence packet -> LLM prose-only interpretation ->
`analyst_notes` in a `bot_report_input.v1` wrapper -> `render_report.py`.

The LLM does not own report layout, metric values, chart values, ranked rows,
evidence limits, or final HTML/Markdown. Those remain deterministic renderer
responsibilities.

Every wired report follows the same two-state contract — when local
credentials resolve, the firewall is on and capture runs the SQL directly;
when credentials don't resolve, the script emits a
`bot_hydrolix_mcp_query_request.v1` packet, the LLM runs MCP exactly once with
that packet's `cluster` and `validated_sql`, and the script resumes with
`--raw-input`. See [Query Execution Boundary](#query-execution-boundary) for
the canonical statement and [SKILL.md "Data Firewall"](../SKILL.md#data-firewall)
for the decision rule.

| Report type | Required artifact schemas | Creds resolved (firewall on) | No creds (handoff path) | Migration readiness |
|-------------|---------------------------|------------------------------|--------------------------|---------------------|
| `executive_posture` | `bot_posture_movement.v1`; optional `bot_scorecard_index.v1`, `bot_entity_scorecard.v1`, `bot_mover_attribution.v1`, `bot_timeseries.v1` | `bot_insights_report.py --report executive_posture --mode evidence` runs vetted summary SQL via `/query/` and writes the evidence packet locally | Same command exits `42` with handoff packet; rerun with `--raw-input <saved.json>` | Reference implementation |
| `control_review` | `bot_control_review.v1`; optional compatible `bot_posture_movement.v1`, `bot_mover_attribution.v1` | `bot_insights_report.py --report control_review --mode evidence` runs vetted SIEM policy summary SQL via `/query/` and writes the evidence packet locally | Same command exits `42`; rerun with `--raw-input <saved.json>` | Migrated |
| `scorecard_brief` | One `bot_entity_scorecard.v1`; optional compatible `bot_scorecard_index.v1` | `bot_insights_report.py --report scorecard_brief --mode evidence` runs scorecard-grain summary SQL via `/query/`, then `scorecard.py` produces the artifact and evidence packet locally | Same command exits `42`; rerun with `--raw-input <saved.json>` | Migrated |
| `soc_triage` | `bot_scorecard_index.v1`; optional compatible `bot_entity_scorecard.v1`, `bot_posture_movement.v1`, `bot_mover_attribution.v1` | `bot_insights_report.py --report soc_triage --mode evidence` runs SIEM policy summary SQL via `/query/`, then `scorecard.py --domains security_evidence` produces the SOC artifact and evidence packet locally | Same command exits `42`; rerun with `--raw-input <saved.json>` | Migrated |
| `crawler_governance` | One or more `bot_entity_scorecard.v1` artifacts with evaluated `crawler_governance` features; optional compatible `bot_scorecard_index.v1`, `bot_posture_movement.v1`, `bot_mover_attribution.v1` | `bot_insights_report.py --report crawler_governance --mode evidence` runs crawler-grain `bi_summary_*` SQL via `/query/`, then `scorecard.py --domains crawler_governance` produces the artifact and evidence packet locally | Same command exits `42`; rerun with `--raw-input <saved.json>` | Migrated |
| `edge_ops_impact` | One or more `bot_entity_scorecard.v1` artifacts with evaluated `cache_busting` or `origin_impact` features; optional compatible `bot_scorecard_index.v1`, `bot_posture_movement.v1`, `bot_mover_attribution.v1` | Not yet wired in `bot_insights_report.py` — capture path-grain aggregate rows through Hydrolix MCP and feed `cache_origin_impact.py` and/or `scorecard.py` directly. Flag this exception when reporting | Same — exploratory MCP path until orchestration lands | Medium: needs scorecard/evidence-packet alignment for Edge/Ops artifacts |

Next migration work: `edge_ops_impact`. It shares the scorecard-evidence-packet
shape `scorecard_brief`, `soc_triage`, and `crawler_governance` use, so the
increment is small.

## Query Execution Boundary

This section restates the two credential states for predefined report
captures. The canonical statement of the policy and the decision rule for
when MCP is forbidden vs. required lives in
[SKILL.md "Data Firewall"](../SKILL.md#data-firewall).

**Creds resolved (firewall on).** When `~/.config/hydrolix/clusters/<cluster>.env`
(or the same `HYDROLIX_HOST`/`HDX_HOSTNAME` plus token or user/password vars)
resolves and no value is an unresolved `op://`, capture runs the validated SQL
through Hydrolix `/query/` HTTP and writes only the JSON result to the local
output path. The LLM never sees the raw response — it sees the producer
script's deterministic artifact (`bot_posture_movement.v1`,
`bot_entity_scorecard.v1`, etc.) and the `bot_report_evidence.v1` packet built
from it. The LLM MUST NOT call `mcp__*__run_select_query` for this report's
data in this state.

**No creds (handoff path).** When credentials are absent or an `op://`
reference can't be resolved by `op run --env-file`, capture prints a
`bot_hydrolix_mcp_query_request.v1` packet and exits with code `42` instead of
treating missing credentials as a query failure. The LLM/agent then runs
Hydrolix MCP `run_select_query` with exactly the packet's `cluster` and
`validated_sql`, saves the complete JSON result to `target_raw_output_path`,
then resumes the same command with `--raw-input`:

```bash
uv run python skills/bot-insights/scripts/bot_insights_report.py \
  --cluster acme \
  --database akamai \
  --report executive_posture \
  --start "2026-05-01T00:00:00Z" \
  --end "2026-05-02T00:00:00Z" \
  --mode evidence \
  --output evidence-packet.json \
  --raw-input /path/from/target_raw_output_path.json
```

Broad, exploratory, or non-Bot-Insights SQL should stay outside the
deterministic capture script and run through the LLM plus Hydrolix MCP workflow.
Do not mix those exploratory queries into a scripted final-report capture.

## Commands

Full deterministic capture plus report rendering through the skill-owned report
script:

```bash
uv run python skills/bot-insights/scripts/bot_insights_report.py \
  --cluster acme \
  --database akamai \
  --report executive_posture \
  --start "2026-05-07T00:00:00Z" \
  --end "2026-05-08T00:00:00Z" \
  --mode report \
  --output posture-report.html \
  --format html
```

Evidence-packet mode for LLM interpretation:

```bash
uv run python skills/bot-insights/scripts/bot_insights_report.py \
  --cluster acme \
  --database akamai \
  --report executive_posture \
  --start "2026-05-07T00:00:00Z" \
  --end "2026-05-08T00:00:00Z" \
  --mode evidence \
  --output evidence-packet.json
```

Template mode writes a Markdown scaffold with deterministic evidence and
explicit LLM fill-in instructions:

```bash
uv run python skills/bot-insights/scripts/bot_insights_report.py \
  --cluster acme \
  --database akamai \
  --report executive_posture \
  --start "2026-05-07T00:00:00Z" \
  --end "2026-05-08T00:00:00Z" \
  --mode template \
  --output llm-template.md
```

`~/src/utils/bot-insights-report` is a thin executable convenience wrapper for
the same script:

```bash
~/src/utils/bot-insights-report --help
```

## Skill-Orchestrated LLM Reports

When the user asks the skill to produce the final report, use the LLM as an
interpretation handoff, not as the report renderer. The deterministic data path
is:

1. Run `bot_insights_report.py --mode evidence` for the requested scope.
2. If and only if the script exits `42` with a
   `bot_hydrolix_mcp_query_request.v1` packet, run Hydrolix MCP
   `run_select_query` with exactly the packet's `cluster` and `validated_sql`,
   save the complete JSON response to `target_raw_output_path`, then rerun the
   same evidence command with `--raw-input`.
3. Send the resulting `bot_report_evidence.v1` packet to the LLM with the
   packet's `interpretation_contract`. The LLM should return prose only.
4. Build a `bot_report_input.v1` wrapper that contains the deterministic
   artifact(s) plus one `analyst_notes` entry for the LLM prose.
5. Render that wrapper with `render_report.py`.

The final wrapper should keep interpretation and evidence separate:

```json
{
  "schema_version": "bot_report_input.v1",
  "report_type": "executive_posture",
  "title": "Bot Insights Executive Posture",
  "artifacts": [
    {
      "schema_version": "bot_posture_movement.v1"
    }
  ],
  "analyst_notes": [
    {
      "note_id": "executive-interpretation",
      "author_type": "llm",
      "title": "Executive Interpretation",
      "text": "Concise prose generated from the evidence packet only.",
      "show_data_sources": false,
      "data_sources": []
    }
  ]
}
```

The example above is intentionally partial; replace `artifacts` with the full
deterministic artifact objects saved by the evidence workflow. Add citation
objects to `data_sources` only when the visible report should show supporting
values under the note. Otherwise, leave `show_data_sources: false` so the
charts, tables, and timeline carry the evidence.

The report script calls
[../scripts/bot_insights_capture.py](../scripts/bot_insights_capture.py) for
SQL generation, guardrail validation, credential detection, and optional direct
Hydrolix access. Capture supports vetted presets such as `posture-overview`,
`posture-by-asn`, `posture-by-path`, and `siem-policy`, plus guarded Bot
Insights summary SQL with explicit time predicates. If capture returns an MCP
handoff packet, save the MCP query result and resume with `--raw-input`.

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

Use `bot_insights_capture.py` for deterministic Bot Insights report captures, or
use the Hydrolix MCP server/host Hydrolix query tool to produce aggregate rows
for broader investigation. Run `compare_posture.py` or `scorecard.py` to emit
artifact JSON. Only then does `render_report.py` consume those saved artifacts.

For LLM-assisted reports, pass the `bot_report_evidence.v1` evidence packet to
the LLM and require it to fill only the template sections from packet fields.
The LLM may write prose, but must not query Hydrolix, invent missing metrics,
or make root-cause or malicious-traffic claims without additional artifacts.

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
