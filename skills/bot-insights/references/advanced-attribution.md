# bot-insights - Advanced Attribution

Advanced attribution normalizes Bot Insights aggregate rows into a conservative
`bot_attribution_report.v1` report. It is for explaining which grouped entities
account for an observed aggregate delta for one metric and one dimension set per
run.

The CLI is a local normalizer. It does not query Hydrolix, open database
clients, read credentials, or export scorecard artifacts. Use Hydrolix MCP or a
host query tool to produce the aggregate rows first, then pass the small result
set to `scripts/attribution.py`.

## Legacy Boundary

Keep `bot_mover_attribution.v1` as the legacy/simple mover schema. It is emitted
by `scripts/compare_posture.py` for existing simple posture, single-dimension
mover, and control-review workflows.

Use `bot_attribution_report.v1` as the advanced attribution target schema. It is
emitted by `scripts/attribution.py` for aggregate-delta attribution reports that
need explicit row-shape normalization, lifecycle/support labels, conservative
confidence caps, and limitations metadata.

Do not grow `compare_posture.py` into the advanced attribution engine. Do not
replace simple mover packets with advanced attribution when the existing
`bot_mover_attribution.v1` output already satisfies the workflow.

## CLI Usage

Run the CLI from the repository root:

```sh
uv run python skills/bot-insights/scripts/attribution.py --file aggregate.json --metric requests --dimensions client_asn
```

The same payload can be passed through stdin:

```sh
cat aggregate.json | uv run python skills/bot-insights/scripts/attribution.py --metric requests --dimensions client_asn
```

The standalone CLI exposes only report output:

```sh
uv run python skills/bot-insights/scripts/attribution.py --file aggregate.json --output report
```

Options:

- `--file`: read aggregate JSON from a file.
- positional JSON: read pasted JSON from command arguments.
- stdin: read aggregate JSON when no file or positional JSON is provided.
- `--metric`: select a reviewed metric such as `requests`, `cnt_all`,
  `blocked_requests`, or `bot_share_pct`.
- `--dimensions`: comma-separated dimension columns, such as `client_asn` or
  `request_host,bot_class`.
- `--analysis policy_displacement`: emit a policy-change displacement review
  using the same conservative attribution schema.
- `--min-count`: minimum current and baseline support for medium confidence.
- `--limit`: optional maximum number of ranked movers in the returned report.
- `--output report`: the only v1a output mode.

## Policy Displacement Mode

Use policy displacement mode after a known policy, mitigation, cache-key,
routing, or bot-control change when the question is "where did traffic move?"
rather than only "did the target metric move?"

```sh
uv run python skills/bot-insights/scripts/attribution.py \
  --file aggregate.json \
  --metric requests \
  --dimensions request_host,bot_class \
  --analysis policy_displacement
```

Inputs remain aggregate current/baseline or before/after rows. Add policy-review
metadata when available:

```json
{
  "analysis_type": "policy_displacement",
  "metric": "requests",
  "dimensions": ["request_host"],
  "comparison_type": "post_policy_vs_baseline",
  "policy_change": {
    "name": "block suspicious crawler policy",
    "changed_at": "2026-04-15T12:00:00Z"
  },
  "target_effect": {
    "metric": "blocked_requests",
    "direction": "increase"
  },
  "rows": [
    {
      "request_host": "api.example.com",
      "current_requests": 700,
      "baseline_requests": 300
    }
  ]
}
```

The output still uses `bot_attribution_report.v1`, but sets
`analysis_type: policy_displacement`, uses
`method: policy_displacement_attribution`, preserves policy metadata, and adds
`displacement_summary` with positive delta, negative delta, net delta, largest
increase, and largest decrease across returned rows.

Policy displacement output is a ranked review queue. It does not prove that the
policy caused the movement; pair it with external change evidence and collateral
checks before declaring a policy successful.

## Accepted Input Shapes

The input must contain aggregate rows. Public JSON may be a saved MCP result,
pasted JSON, a wrapper object, or a direct list of row objects.

MCP-style rows with columns:

```json
{
  "metric": "requests",
  "dimensions": ["client_asn"],
  "table_used": "bot_summary_day",
  "columns": ["client_asn", "current_requests", "baseline_requests"],
  "rows": [
    ["64500", 180, 100],
    ["64501", 120, 100]
  ]
}
```

List-of-dict rows:

```json
[
  {
    "client_asn": "64500",
    "current_requests": 180,
    "baseline_requests": 100
  },
  {
    "client_asn": "64501",
    "current_requests": 120,
    "baseline_requests": 100
  }
]
```

Wrapped saved results:

```json
{
  "mcp_result": {
    "metric": "cnt_all",
    "dimensions": ["client_asn"],
    "rows": [
      {
        "client_asn": "64500",
        "current_cnt_all": 180,
        "baseline_cnt_all": 100
      }
    ]
  }
}
```

Period-split rows:

```json
{
  "metric": "requests",
  "dimensions": ["client_asn"],
  "rows": [
    {
      "period": "current",
      "client_asn": "64500",
      "requests": 180
    },
    {
      "period": "baseline",
      "client_asn": "64500",
      "requests": 100
    }
  ]
}
```

Rows must use one shape per run. Do not mix combined current/baseline columns
with period-split rows in the same payload.

## Output Contract

`bot_attribution_report.v1` includes:

- selected metric and metric kind;
- one ordered dimension set;
- normalized row shape;
- ranked movers with current, baseline, absolute delta, percentage change,
  direction, lifecycle, support-change label, and confidence metadata;
- returned-row totals and buckets;
- not-evaluated components for withheld contribution or lifecycle details;
- limitations and interpretation constraints.

V1a contribution is intentionally withheld from public JSON. Reports set
`contribution_basis` to `none`, expose `rowset_complete` as `false`, and include
a not-evaluated `contribution_pct` component.

## Confidence Caps

Public, file, stdin, saved, and pasted JSON is caller-editable input. The v1a
standalone CLI therefore caps report and mover confidence below high confidence.

Typical results:

- `medium`: summary-backed aggregate rows with enough current and baseline
  support and no stronger caveats.
- `low`: sparse counts, raw-table fallback, poor metadata, missing lifecycle
  support, or unevaluated one-sided period absence.

Caller fields such as `rowset_complete`, `contribution_basis`,
`complete_scope_total_abs_delta`, or `scorecard_export_safe` are preserved only
as `input_assertions` when relevant. They do not raise confidence and do not
unlock contribution math.

## Scorecard Boundary

V1a advanced attribution has no scorecard export command, flag, alternate
output mode, or scorecard-safe artifact. `scripts/attribution.py` emits only
`bot_attribution_report.v1` or `bot_attribution_error.v1`.

Use `scripts/scorecard.py` only with scorecard-ready aggregate rows produced for
that script. Do not pass `bot_attribution_report.v1` as scorecard input.

## Interpretation

Attribution reports explain aggregate movement; they do not prove causality.
Use the emitted `limitations`, `confidence_reasons`, and
`interpretation_constraints` when summarizing results for users.
