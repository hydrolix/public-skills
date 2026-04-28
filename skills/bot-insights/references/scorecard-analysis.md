# bot-insights - Scorecard Analysis

Bot Insights scorecards are artifact generation and prioritization, not
dashboards. Dashboards show panels for exploration. Scorecards synthesize
cross-surface evidence into deterministic investigation packets that can be
saved, compared, handed to another workflow, or summarized by an LLM without
letting the LLM invent scores.

Use `scripts/scorecard.py` after Hydrolix has produced small aggregate rows.
The script accepts Hydrolix MCP result JSON, saved aggregate JSON, or pasted
JSON and emits:

- `bot_entity_scorecard.v1` packets for each entity.
- `bot_scorecard_index.v1` ranking entities by rule-based score.

The script does not query Hydrolix, open database clients, read credentials, or
perform forecast, correlation, ML, or opaque classification.

## Contents

- [Workflow](#workflow)
- [Rowset And Feature Provenance](#rowset-and-feature-provenance)
- [Producer Limit Metadata](#producer-limit-metadata)
- [Rule Domains](#rule-domains)
- [Summary-First Table Selection](#summary-first-table-selection)
- [Scorecard-Ready Columns](#scorecard-ready-columns)
- [SQL Templates](#sql-templates)
- [Example Input](#example-input)
- [Example Output](#example-output)

## Workflow

1. Pick the report lens and entity type. Supported entity types are
   `client_asn`, `request_path_norm`, `request_host`, `bot_class`, or
   `ai_category`.
2. Start from the narrowest summary table whose retained dimensions fit that
   lens, entity, and requested scope. For SOC/security scorecards, seed the
   entity population from `bi_siem_summary_*`; do not reuse an Edge/Ops,
   crawler, or posture top-N population unless that is the explicit scope.
3. Aggregate current and baseline windows in Hydrolix, returning one row per
   entity with scorecard-ready fields.
4. Add SIEM enrichment only when security action or policy evidence is needed.
5. Fall back to request-level tables only when required dimensions or features
   are unavailable in summaries, and state the fallback reason.
6. Run `scripts/scorecard.py` on the aggregate JSON to create reusable packets.
   Pass `analysis_domains` in the input JSON, or `--domains` on the CLI, when
   generating a lens-specific scorecard such as `security_evidence` for SOC or
   `crawler_governance` for crawler reports.

Missing fields are not interpreted as safe behavior. They are emitted in
`not_evaluated_features` and reflected in confidence reasons such as
`feature_input_missing` or `siem_unavailable`.

Contribution scoring requires total-scope evidence. Prefer computing
`contribution_pct` or `contribution_to_total_delta_pct` in Hydrolix over the
full grouped scope before applying any result `LIMIT`. The script only
auto-computes missing contribution percentages when the input metadata
explicitly proves completeness with `rowset_complete: true` or
`contribution_basis: "complete_scope"`. Limited or filtered rowsets without
that metadata leave `contribution_to_total_delta_high` unevaluated.

Use one input row shape per run. Rows may be already-combined entity rows with
`current_*` and `baseline_*` fields, or period-split rows with `period` values
of `"current"`/`"baseline"` that the script combines. Do not mix those row
shapes in one payload; normalize or join enrichment before running
`scorecard.py`.

## Rowset And Feature Provenance

Callers may supply structured provenance on the payload or on individual rows so
that downstream report renderers can prove that generic rate features such as
`rate_429_delta_high` or `rate_5xx_delta_high` came from a crawler-specific
rowset. The script preserves these fields on emitted
`bot_entity_scorecard.v1` artifacts but does not synthesize them.

Supported fields:

- `rowset_scope.population`, when present, must be one of `crawler`,
  `good_bot`, `ai_crawler`, `all_traffic`, or `unknown`. Other
  `rowset_scope` fields (such as `filters`, `entity_type`, and `table_used`)
  are passed through unchanged.
- `feature_provenance` must be a JSON object keyed by scorecard feature name.
  Each entry may carry its own `rowset_scope`, a `metric_inputs` array of
  strings naming the aggregate inputs, and free-form `notes`.
- `feature_provenance.<feature>.metric_inputs`, when present, must be an array
  of strings.

Row-level `rowset_scope` and `feature_provenance` override payload-level
values on the emitted scorecard. Feature-level provenance is preserved so a
renderer can resolve feature-specific populations over artifact-level ones.
Invalid provenance shapes fail closed with an explicit error so artifacts stay
deterministic.
For period-split rows that the script combines into one entity row, matching
row-level provenance is preserved; conflicting per-period provenance is a hard
failure instead of last-row-wins merge behavior.

## Producer Limit Metadata

`scripts/scorecard.py --limit <n>` truncates scorecards and ranked index
entries before they reach a renderer. Producer-limit metadata is emitted only
on metadata-capable outputs:

- Default `bot_scorecard_artifacts.v1` output carries `producer_limit`,
  `result_row_count`, `result_truncated`, and `total_ranked_entities` at the
  packet level, and the embedded `bot_scorecard_index.v1` carries the same
  fields.
- `--output index` emits `bot_scorecard_index.v1` with `producer_limit`,
  `result_row_count`, `result_truncated`, and `total_ranked_entities`.
- `--output scorecards` intentionally emits a bare JSON list of
  `bot_entity_scorecard.v1` artifacts. A bare list has no packet-level
  location for `producer_limit`, `result_row_count`, or `result_truncated`;
  downstream renderers must treat the list as the emitted known collection
  rather than as proof of the upstream population.

## Rule Domains

The MVP actively scores these domains:

- `movement`: new entities, volume deltas, total-delta contribution, bot-share
  movement.
- `origin_impact`: origin p95 movement and origin cost contribution.
- `cache_busting`: cache miss rate, cache miss movement, query-string
  diversity, and query-string diversity with high miss rate.
- `crawler_governance`: 429/5xx movement, good bot rate limiting, good bot
  error rate, governance-surface failures, AI crawler growth.
- `security_evidence`: SIEM blocked/auth-fail evidence and bad bot share.
- `policy_collateral`: good bot collateral 429s, protected-population error
  rates, and displacement movement after a policy or control change.

`signal_alignment` is reserved for future scorecard inputs. When any optional
domain inputs are unavailable, do not score them as zero-risk substitutes; leave
the evidence unevaluated or add explicit feature inputs in a future schema
revision.

## Summary-First Table Selection

- Use `akamai.bi_summary_day`, `akamai.bi_summary_hour`, or
  `akamai.bi_summary_minute` for Akamai-project host, ASN, bot class, AI
  category, bot share, cache miss rate, 429/5xx rate, and origin latency when
  those retained dimensions answer the question. Use `bot_summary_*` only when
  metadata proves the preferred `bi_summary_*` family is absent or unsuitable.
- Use `bot_agg_path_day`, `bot_agg_path_hour`, or `bot_agg_path_minute` for
  normalized path scorecards, especially query-string diversity and cache miss
  evidence.
- Use `bot_agg_asn_hour` when ASN drilldowns need unique normalized paths.
- Use `akamai.bi_siem_summary_*` for SIEM blocked requests, auth failures, and
  policy/action evidence on the Akamai project. Treat `akamai.bi_summary_siem_*`
  as an equivalent deployment-specific alias only when metadata shows that
  exact table exists. Use `bot_siem_summary_*`, `bot_siem_filter_summary_*`, or
  `bot_siem_class_*` only when the preferred `bi_*` SIEM surface is absent or
  lacks the retained dimensions needed for the question.
- Fall back to `bot_detection` or `bot_detection_siem` only for fields not
  retained in summaries, such as exact user agent, verified owner,
  verification tier, bot confidence, attack payload details, exact query
  strings, or exact status-code inspection.

If Hydrolix metadata reports aggregate-state columns, replace `sum(metric)`
with the merge function reported by the table metadata tool.

## Scorecard-Ready Columns

The script recognizes current/baseline prefixes such as:

- `current_requests`, `baseline_requests`
- `current_bot_share_pct`, `baseline_bot_share_pct`
- `current_cache_miss_pct`, `baseline_cache_miss_pct`
- `current_origin_p95_ms`, `baseline_origin_p95_ms`
- `current_rate_429_pct`, `baseline_rate_429_pct`
- `current_rate_5xx_pct`, `baseline_rate_5xx_pct`
- `current_ai_crawler_requests`, `baseline_ai_crawler_requests`

It also accepts current-only fields such as:

- `contribution_pct` or `contribution_to_total_delta_pct`
- `qs_diversity_ratio`
- `origin_cost_contribution_pct`
- `good_bot_429_requests`
- `good_bot_error_rate_pct`
- `policy_surface_failures`
- `siem_blocked_requests`
- `siem_auth_fail_requests`
- `bad_bot_share_pct`
- `good_bot_collateral_429_requests`
- `policy_collateral_error_rate_pct`

Policy displacement fields should be provided as current/baseline pairs:

- `current_displacement_requests`, `baseline_displacement_requests`

## SQL Templates

These templates intentionally omit clients, credentials, and execution logic.
Use the Hydrolix MCP server or host query tool to run them.

Replace `<posture_summary_day>` / `<posture_summary_hour>` with
`akamai.bi_summary_day` / `akamai.bi_summary_hour` for Akamai-project
scorecards. Replace `<siem_summary_hour>` with `akamai.bi_siem_summary_hour`
unless metadata confirms a deployment-specific `akamai.bi_summary_siem_hour`
alias. Use `bot_summary_*` or `bot_siem_summary_*` only as metadata-confirmed
fallbacks.

### ASN Scorecards

```sql
WITH
  toDateTime('<current_start>') AS current_start,
  toDateTime('<current_end>') AS current_end,
  toDateTime('<baseline_start>') AS baseline_start,
  toDateTime('<baseline_end>') AS baseline_end,
  by_entity AS (
    SELECT
      client_asn,
      sumIf(cnt_all, timestamp >= current_start AND timestamp < current_end) AS current_requests,
      sumIf(cnt_all, timestamp >= baseline_start AND timestamp < baseline_end) AS baseline_requests,
      round(
        sumIf(cnt_all, timestamp >= current_start AND timestamp < current_end AND is_bot_traffic = true)
        / greatest(current_requests, 1) * 100, 2
      ) AS current_bot_share_pct,
      round(
        sumIf(cnt_all, timestamp >= baseline_start AND timestamp < baseline_end AND is_bot_traffic = true)
        / greatest(baseline_requests, 1) * 100, 2
      ) AS baseline_bot_share_pct,
      round(
        sumIf(cnt_cache_miss, timestamp >= current_start AND timestamp < current_end)
        / greatest(current_requests, 1) * 100, 2
      ) AS current_cache_miss_pct,
      round(
        sumIf(cnt_cache_miss, timestamp >= baseline_start AND timestamp < baseline_end)
        / greatest(baseline_requests, 1) * 100, 2
      ) AS baseline_cache_miss_pct,
      maxIf(p95_origin_ttfb, timestamp >= current_start AND timestamp < current_end) AS current_origin_p95_ms,
      maxIf(p95_origin_ttfb, timestamp >= baseline_start AND timestamp < baseline_end) AS baseline_origin_p95_ms,
      round(
        sumIf(cnt_429, timestamp >= current_start AND timestamp < current_end)
        / greatest(current_requests, 1) * 100, 2
      ) AS current_rate_429_pct,
      round(
        sumIf(cnt_429, timestamp >= baseline_start AND timestamp < baseline_end)
        / greatest(baseline_requests, 1) * 100, 2
      ) AS baseline_rate_429_pct,
      round(
        sumIf(cnt_5xx, timestamp >= current_start AND timestamp < current_end)
        / greatest(current_requests, 1) * 100, 2
      ) AS current_rate_5xx_pct,
      round(
        sumIf(cnt_5xx, timestamp >= baseline_start AND timestamp < baseline_end)
        / greatest(baseline_requests, 1) * 100, 2
      ) AS baseline_rate_5xx_pct
    FROM <project>.<posture_summary_hour>
    WHERE timestamp >= baseline_start
      AND timestamp < current_end
      AND request_host = '<host>'
    GROUP BY client_asn
  )
SELECT
  client_asn,
  current_requests,
  baseline_requests,
  current_bot_share_pct,
  baseline_bot_share_pct,
  current_cache_miss_pct,
  baseline_cache_miss_pct,
  current_origin_p95_ms,
  baseline_origin_p95_ms,
  current_rate_429_pct,
  baseline_rate_429_pct,
  current_rate_5xx_pct,
  baseline_rate_5xx_pct,
  abs(current_requests - baseline_requests)
    / greatest(sum(abs(current_requests - baseline_requests)) OVER (), 1) * 100 AS contribution_pct,
  current_requests * current_origin_p95_ms
    / greatest(sum(current_requests * current_origin_p95_ms) OVER (), 1) * 100 AS origin_cost_contribution_pct
FROM by_entity
ORDER BY abs(current_requests - baseline_requests) DESC
LIMIT 50
```

### Path Scorecards

```sql
WITH
  toDateTime('<current_start>') AS current_start,
  toDateTime('<current_end>') AS current_end,
  toDateTime('<baseline_start>') AS baseline_start,
  toDateTime('<baseline_end>') AS baseline_end
SELECT
  request_path_norm,
  sumIf(cnt_all, timestamp >= current_start AND timestamp < current_end) AS current_requests,
  sumIf(cnt_all, timestamp >= baseline_start AND timestamp < baseline_end) AS baseline_requests,
  round(sumIf(uniq_qs, timestamp >= current_start AND timestamp < current_end) / greatest(current_requests, 1), 4) AS qs_diversity_ratio,
  round(sumIf(cnt_cache_miss, timestamp >= current_start AND timestamp < current_end) / greatest(current_requests, 1) * 100, 2) AS current_cache_miss_pct,
  round(sumIf(cnt_cache_miss, timestamp >= baseline_start AND timestamp < baseline_end) / greatest(baseline_requests, 1) * 100, 2) AS baseline_cache_miss_pct,
  maxIf(p95_origin_ttfb, timestamp >= current_start AND timestamp < current_end) AS current_origin_p95_ms,
  maxIf(p95_origin_ttfb, timestamp >= baseline_start AND timestamp < baseline_end) AS baseline_origin_p95_ms,
  round(sumIf(cnt_429, timestamp >= current_start AND timestamp < current_end) / greatest(current_requests, 1) * 100, 2) AS current_rate_429_pct,
  round(sumIf(cnt_429, timestamp >= baseline_start AND timestamp < baseline_end) / greatest(baseline_requests, 1) * 100, 2) AS baseline_rate_429_pct,
  round(sumIf(cnt_5xx, timestamp >= current_start AND timestamp < current_end) / greatest(current_requests, 1) * 100, 2) AS current_rate_5xx_pct,
  round(sumIf(cnt_5xx, timestamp >= baseline_start AND timestamp < baseline_end) / greatest(baseline_requests, 1) * 100, 2) AS baseline_rate_5xx_pct
FROM <project>.bot_agg_path_hour
WHERE timestamp >= baseline_start
  AND timestamp < current_end
  AND request_host = '<host>'
GROUP BY request_path_norm
HAVING current_requests > 100 OR baseline_requests > 100
ORDER BY qs_diversity_ratio DESC, current_requests DESC
LIMIT 50
```

### Host Scorecards

```sql
WITH
  toDateTime('<current_start>') AS current_start,
  toDateTime('<current_end>') AS current_end,
  toDateTime('<baseline_start>') AS baseline_start,
  toDateTime('<baseline_end>') AS baseline_end
SELECT
  request_host,
  sumIf(cnt_all, timestamp >= current_start AND timestamp < current_end) AS current_requests,
  sumIf(cnt_all, timestamp >= baseline_start AND timestamp < baseline_end) AS baseline_requests,
  round(sumIf(cnt_all, timestamp >= current_start AND timestamp < current_end AND is_bot_traffic = true) / greatest(current_requests, 1) * 100, 2) AS current_bot_share_pct,
  round(sumIf(cnt_all, timestamp >= baseline_start AND timestamp < baseline_end AND is_bot_traffic = true) / greatest(baseline_requests, 1) * 100, 2) AS baseline_bot_share_pct,
  round(sumIf(cnt_all, timestamp >= current_start AND timestamp < current_end AND bot_class = 'bad') / greatest(current_requests, 1) * 100, 2) AS bad_bot_share_pct,
  round(sumIf(cnt_cache_miss, timestamp >= current_start AND timestamp < current_end) / greatest(current_requests, 1) * 100, 2) AS current_cache_miss_pct,
  round(sumIf(cnt_cache_miss, timestamp >= baseline_start AND timestamp < baseline_end) / greatest(baseline_requests, 1) * 100, 2) AS baseline_cache_miss_pct,
  maxIf(p95_origin_ttfb, timestamp >= current_start AND timestamp < current_end) AS current_origin_p95_ms,
  maxIf(p95_origin_ttfb, timestamp >= baseline_start AND timestamp < baseline_end) AS baseline_origin_p95_ms,
  round(sumIf(cnt_429, timestamp >= current_start AND timestamp < current_end) / greatest(current_requests, 1) * 100, 2) AS current_rate_429_pct,
  round(sumIf(cnt_429, timestamp >= baseline_start AND timestamp < baseline_end) / greatest(baseline_requests, 1) * 100, 2) AS baseline_rate_429_pct,
  round(sumIf(cnt_5xx, timestamp >= current_start AND timestamp < current_end) / greatest(current_requests, 1) * 100, 2) AS current_rate_5xx_pct,
  round(sumIf(cnt_5xx, timestamp >= baseline_start AND timestamp < baseline_end) / greatest(baseline_requests, 1) * 100, 2) AS baseline_rate_5xx_pct,
  sumIf(cnt_all, timestamp >= current_start AND timestamp < current_end AND ai_category != '') AS current_ai_crawler_requests,
  sumIf(cnt_all, timestamp >= baseline_start AND timestamp < baseline_end AND ai_category != '') AS baseline_ai_crawler_requests,
  sumIf(cnt_429, timestamp >= current_start AND timestamp < current_end AND bot_class IN ('good', 'crawler')) AS good_bot_429_requests,
  round(
    sumIf(cnt_5xx, timestamp >= current_start AND timestamp < current_end AND bot_class IN ('good', 'crawler'))
    / greatest(sumIf(cnt_all, timestamp >= current_start AND timestamp < current_end AND bot_class IN ('good', 'crawler')), 1) * 100, 2
  ) AS good_bot_error_rate_pct
FROM <project>.<posture_summary_day>
WHERE timestamp >= baseline_start
  AND timestamp < current_end
GROUP BY request_host
ORDER BY abs(current_requests - baseline_requests) DESC
LIMIT 50
```

### AI Category Scorecards

```sql
WITH
  toDateTime('<current_start>') AS current_start,
  toDateTime('<current_end>') AS current_end,
  toDateTime('<baseline_start>') AS baseline_start,
  toDateTime('<baseline_end>') AS baseline_end
SELECT
  ai_category,
  sumIf(cnt_all, timestamp >= current_start AND timestamp < current_end) AS current_requests,
  sumIf(cnt_all, timestamp >= baseline_start AND timestamp < baseline_end) AS baseline_requests,
  current_requests AS current_ai_crawler_requests,
  baseline_requests AS baseline_ai_crawler_requests,
  round(sumIf(cnt_cache_miss, timestamp >= current_start AND timestamp < current_end) / greatest(current_requests, 1) * 100, 2) AS current_cache_miss_pct,
  round(sumIf(cnt_cache_miss, timestamp >= baseline_start AND timestamp < baseline_end) / greatest(baseline_requests, 1) * 100, 2) AS baseline_cache_miss_pct,
  round(sumIf(cnt_429, timestamp >= current_start AND timestamp < current_end) / greatest(current_requests, 1) * 100, 2) AS current_rate_429_pct,
  round(sumIf(cnt_429, timestamp >= baseline_start AND timestamp < baseline_end) / greatest(baseline_requests, 1) * 100, 2) AS baseline_rate_429_pct,
  round(sumIf(cnt_5xx, timestamp >= current_start AND timestamp < current_end) / greatest(current_requests, 1) * 100, 2) AS current_rate_5xx_pct,
  round(sumIf(cnt_5xx, timestamp >= baseline_start AND timestamp < baseline_end) / greatest(baseline_requests, 1) * 100, 2) AS baseline_rate_5xx_pct
FROM <project>.<posture_summary_day>
WHERE timestamp >= baseline_start
  AND timestamp < current_end
  AND ai_category != ''
GROUP BY ai_category
ORDER BY current_requests DESC
```

### Crawler Governance Enrichment

Run this over the same `bi_summary_*` posture table used for the base
scorecard when the scorecard should support the `crawler_governance` report
lens. Join the returned fields into the scorecard row by entity before calling
`scorecard.py`. Preserve zero values; a zero count is evaluated evidence, while
a missing field becomes `feature_input_missing`.

```sql
WITH
  toDateTime('<current_start>') AS current_start,
  toDateTime('<current_end>') AS current_end,
  toDateTime('<baseline_start>') AS baseline_start,
  toDateTime('<baseline_end>') AS baseline_end
SELECT
  request_host,
  sumIf(cnt_all, timestamp >= current_start AND timestamp < current_end AND ai_category != '') AS current_ai_crawler_requests,
  sumIf(cnt_all, timestamp >= baseline_start AND timestamp < baseline_end AND ai_category != '') AS baseline_ai_crawler_requests,
  sumIf(cnt_429, timestamp >= current_start AND timestamp < current_end AND bot_class IN ('good', 'crawler')) AS good_bot_429_requests,
  round(
    sumIf(cnt_5xx, timestamp >= current_start AND timestamp < current_end AND bot_class IN ('good', 'crawler'))
    / greatest(sumIf(cnt_all, timestamp >= current_start AND timestamp < current_end AND bot_class IN ('good', 'crawler')), 1) * 100, 2
  ) AS good_bot_error_rate_pct,
  0 AS policy_surface_failures
FROM <project>.<posture_summary_hour>
WHERE timestamp >= baseline_start
  AND timestamp < current_end
GROUP BY request_host
```

For aggregate-state summary tables such as `akamai.bi_summary_hour`, use the
metadata-reported merge functions directly, for example
`countMergeIf(\`count()\`, ...)`, `countIfMergeIf(\`countIf(...429...)\`, ...)`,
and `countIfMergeIf(\`countIf(...500...)\`, ...)`. The fields emitted to
`scorecard.py` stay canonical: `current_ai_crawler_requests`,
`baseline_ai_crawler_requests`, `good_bot_429_requests`,
`good_bot_error_rate_pct`, and `policy_surface_failures`.

### SOC Security Evidence Scorecards

For SOC triage, start from the SIEM-active population and evaluate only the
`security_evidence` domain. This prevents a SOC scorecard from inheriting an
Edge/Ops host list that has no SIEM rows, and it prevents unrelated cache,
origin, crawler, or policy-collateral inputs from appearing as missing SOC
evidence. Use `akamai.bi_siem_summary_hour` on the Akamai project unless
metadata proves a different SIEM summary table is required.

```sql
WITH
  toDateTime('<current_start>') AS current_start,
  toDateTime('<current_end>') AS current_end,
  toDateTime('<baseline_start>') AS baseline_start,
  toDateTime('<baseline_end>') AS baseline_end
SELECT
  request_host,
  countMergeIf(`count()`, timestamp >= current_start AND timestamp < current_end) AS current_requests,
  countMergeIf(`count()`, timestamp >= baseline_start AND timestamp < baseline_end) AS baseline_requests,
  countIfMergeIf(
    `countIf(or(equals(action_taken, 'deny'), equals(action_taken, 'block')))`,
    timestamp >= current_start AND timestamp < current_end
  ) AS siem_blocked_requests,
  countIfMergeIf(
    `countIf(equals(auth_outcome, 'fail'))`,
    timestamp >= current_start AND timestamp < current_end
  ) AS siem_auth_fail_requests,
  0 AS bad_bot_share_pct
FROM <project>.<siem_summary_hour>
WHERE timestamp >= baseline_start
  AND timestamp < current_end
GROUP BY request_host
HAVING current_requests > 0 OR siem_blocked_requests > 0 OR siem_auth_fail_requests > 0
ORDER BY siem_blocked_requests DESC, siem_auth_fail_requests DESC, current_requests DESC
LIMIT 50
```

Wrap the rows with lens metadata before running the script:

```json
{
  "entity_type": "request_host",
  "analysis_domains": ["security_evidence"],
  "table_used": "akamai.bi_siem_summary_hour",
  "rows": []
}
```

Or pass the lens on the CLI:

```bash
uv run python skills/bot-insights/scripts/scorecard.py \
  --domains security_evidence \
  --file /tmp/soc-scorecard-input.json
```

Replace the `bad_bot_share_pct` placeholder with a posture-summary enrichment
when bad-bot share is needed for the SOC decision. A zero value is evaluated as
"not present"; an omitted value is treated as a missing scorecard input.

### Optional SIEM Enrichment

Run this as an enrichment query when scorecards need security action or policy
evidence. Join the returned rows to the base scorecard rows by entity in the
calling workflow before feeding JSON to `scorecard.py`.

```sql
WITH
  toDateTime('<current_start>') AS current_start,
  toDateTime('<current_end>') AS current_end
SELECT
  client_asn,
  countMerge(`count()`) AS siem_requests,
  countIfMerge(`countIf(or(equals(action_taken, 'deny'), equals(action_taken, 'block')))`) AS siem_blocked_requests,
  countIfMerge(`countIf(equals(auth_outcome, 'fail'))`) AS siem_auth_fail_requests
FROM <project>.<siem_summary_hour>
WHERE timestamp >= current_start
  AND timestamp < current_end
  AND request_host = '<host>'
GROUP BY client_asn
ORDER BY siem_blocked_requests DESC, siem_auth_fail_requests DESC
LIMIT 50
```

## Example Input

```json
{
  "entity_type": "request_path_norm",
  "comparison_type": "week_over_week",
  "granularity": "hour",
  "table_used": "bot_agg_path_hour",
  "current_window": {"start": "2026-04-08T00:00:00Z", "end": "2026-04-15T00:00:00Z"},
  "baseline_windows": [
    {"start": "2026-04-01T00:00:00Z", "end": "2026-04-08T00:00:00Z", "label": "previous_week"}
  ],
  "scope": {"request_host": "www.example.com"},
  "rows": [
    {
      "request_path_norm": "/api/search",
      "current_requests": 82000,
      "baseline_requests": 12000,
      "qs_diversity_ratio": 0.97,
      "current_cache_miss_pct": 94.2,
      "baseline_cache_miss_pct": 32.4,
      "current_origin_p95_ms": 930,
      "baseline_origin_p95_ms": 410,
      "origin_cost_contribution_pct": 42.1,
      "current_rate_429_pct": 8.2,
      "baseline_rate_429_pct": 0.4,
      "bad_bot_share_pct": 76.5,
      "siem_blocked_requests": 1840
    }
  ]
}
```

## Example Output

This output is abbreviated. A full scorecard includes every evaluated feature
and every feature skipped because inputs were missing.

```json
{
  "schema_version": "bot_scorecard_artifacts.v1",
  "scorecards": [
    {
      "schema_version": "bot_entity_scorecard.v1",
      "entity_type": "request_path_norm",
      "entity": "/api/search",
      "scope": {"request_host": "www.example.com"},
      "comparison_type": "week_over_week",
      "granularity": "hour",
      "table_used": "bot_agg_path_hour",
      "current_window": {"start": "2026-04-08T00:00:00Z", "end": "2026-04-15T00:00:00Z"},
      "baseline_windows": [
        {"start": "2026-04-01T00:00:00Z", "end": "2026-04-08T00:00:00Z", "label": "previous_week"}
      ],
      "score": 100,
      "band": "urgent_review",
      "primary_domain": "cache_busting",
      "domain_scores": {
        "movement": 34,
        "origin_impact": 28,
        "cache_busting": 52,
        "crawler_governance": 8,
        "security_evidence": 26,
        "signal_alignment": 0,
        "policy_collateral": 0
      },
      "features": [
        {
          "name": "querystring_diversity_with_high_miss_rate",
          "domain": "cache_busting",
          "points": 18,
          "current": 0.97,
          "threshold": 0.5,
          "supporting_metrics": {"cache_miss_pct": 94.2, "cache_miss_threshold": 50},
          "evidence": "High query-string diversity coincides with 94.2% cache misses."
        }
      ],
      "not_evaluated_features": [
        {
          "name": "good_bot_429_present",
          "domain": "crawler_governance",
          "missing_inputs": ["good_bot_429_requests"],
          "reason": "feature_input_missing"
        }
      ],
      "evidence_summary": [
        "High query-string diversity coincides with 94.2% cache misses."
      ],
      "recommended_next_steps": [
        "Inspect query-string diversity, cache-key behavior, and cache miss concentration by host and path."
      ],
      "confidence": "medium",
      "confidence_reasons": [
        "summary_table_used",
        "retained_dimensions_fit",
        "current_count_sufficient",
        "baseline_count_sufficient",
        "feature_input_missing"
      ],
      "interpretation_constraints": [
        "rule_based_scorecard",
        "mechanical_features_only",
        "no_causal_claim",
        "llm_may_summarize_structured_evidence_only"
      ]
    }
  ],
  "index": {
    "schema_version": "bot_scorecard_index.v1",
    "scope": {"request_host": "www.example.com"},
    "comparison_type": "week_over_week",
    "table_used": "bot_agg_path_hour",
    "current_window": {"start": "2026-04-08T00:00:00Z", "end": "2026-04-15T00:00:00Z"},
    "baseline_windows": [
      {"start": "2026-04-01T00:00:00Z", "end": "2026-04-08T00:00:00Z", "label": "previous_week"}
    ],
    "ranked_entities": [
      {
        "rank": 1,
        "entity_type": "request_path_norm",
        "entity": "/api/search",
        "score": 100,
        "band": "urgent_review",
        "primary_domain": "cache_busting",
        "confidence": "medium"
      }
    ],
    "interpretation_constraints": [
      "rule_based_scorecard",
      "mechanical_features_only",
      "no_causal_claim",
      "llm_may_summarize_structured_evidence_only"
    ]
  }
}
```
