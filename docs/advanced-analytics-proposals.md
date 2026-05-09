# Advanced Analytics Proposals for CDN Insights and Bot Insights

This document captures proposed advanced analytics, prediction, and
interpretation-support functionality for the `cdn-insights` and `bot-insights`
skills.

The core design constraint is that analysis should be mechanical. The LLM may
interpret structured outputs, explain likely implications, and suggest follow-up
questions, but it should not directly classify traffic, infer causality, or make
statistical judgments from raw records.

## Scope

### CDN Insights

The `cdn-insights` skill covers normalized multi-CDN access logs and supports
analysis of:

- Traffic volume and CDN mix.
- Cache efficiency and origin offload.
- Origin and edge latency.
- 4xx and 5xx error rates.
- Host, path, country, ASN, POP, and CDN attribution.
- Current versus baseline comparisons.

CDN Insights has request-level data in `cdn_insights` and summary tables at
minute and hour granularity. Advanced analytics should prefer summary tables
when their retained dimensions fit the question.

### Bot Insights

The `bot-insights` skill extends CDN access logs with bot intelligence and
supports analysis of:

- Bot share, score, class, confidence, intent, and category.
- Verified and unverified bot ownership.
- Good bot health and SEO crawler governance.
- AI crawler monitoring.
- Suspicious crawler spoofing signals.
- Attack data enrichment.
- Bot-driven cache misses, query-string churn, origin load, and bandwidth.

Bot Insights has request-level data plus multiple summary tables at minute,
hour, and day granularity. Advanced analytics should prefer those summaries
when their retained dimensions fit the question, and should fall back to
request-level tables only for fields not retained in the summary surface.

## Design Principles

1. Mechanical outputs first.

   Functions should return structured evidence: metrics, deltas, confidence
   indicators, scoring features, dimensions, and caveats.

2. LLM interpretation second.

   The LLM can summarize, explain, and propose next steps from structured
   evidence. It should not be the component that computes anomaly scores,
   forecasts, risk scores, or attribution.

3. Explicit methods.

   Every result should state the method used, such as `current_vs_baseline`,
   `rolling_median_mad`, `seasonal_hour_of_week_median`, or
   `rule_based_scorecard`.

4. Explainable scoring.

   Risk and severity scores should expose their contributing features and
   weights. Avoid opaque classifiers as a first implementation.

5. Guarded thresholds.

   Percentage changes should include minimum-volume guards. Low-volume slices
   should be marked as low confidence rather than over-ranked.

6. Correlation is not causation.

   Mechanical correlation and lead-lag results should be labeled as
   investigative signals only.

7. Use summary tables where possible.

   CDN summary tables should be used for larger ranges. Bot analytics should
   use the existing minute/hour/day summaries for posture, baseline movement,
   and trend analysis when their retained dimensions fit the question.

## Dependency Policy

External dependencies are acceptable when they have a clear justification. The
default should still be dependency-light, especially for deterministic
attribution and report shaping.

### Good Reasons to Add a Dependency

- Statistical correctness for methods that are easy to get subtly wrong.
- Numerical stability for robust statistics, correlation, regression, and
  forecast intervals.
- Backtesting and validation for forecast quality.
- Maintainability when local code would otherwise become a small statistics
  library.
- Interoperability when tabular result processing becomes too awkward with
  plain Python data structures.

### Poor Reasons to Add a Dependency

- Simple current versus baseline deltas.
- Top movers and contribution math.
- Ratio calculations.
- Rule-based bot or cache-busting scorecards.
- JSON report shaping.
- Basic SQL-driven rolling windows where the database can do the work clearly.

### Candidate Dependency Tiers

| Tier | Dependency | Justification |
|------|------------|---------------|
| 0 | Python standard library and SQL | Deltas, movers, rule-based scoring, report shaping |
| 1 | `numpy` | Vector math, percentile arrays, lightweight numerical operations |
| 1 | `scipy` | Spearman/Pearson correlation, confidence intervals, statistical tests |
| 1 | `pandas` or `polars` | Local tabular transforms, time-series resampling, rolling windows |
| 2 | `statsmodels` | Transparent classical forecasting and backtesting |
| 2 | `ruptures` | Changepoint detection for incident start/end and mitigation verification |
| 2 | `river` | Online drift and anomaly detection if analytics become continuous |
| 3 | `scikit-learn` | Later-stage clustering or unsupervised fingerprints, if justified |

Avoid heavy or opaque dependencies until a specific capability requires them.
Deep learning dependencies are not justified for the current skill scope.

## Proposed Mechanical Capabilities

### 1. Baseline, Posture Movement, and Anomaly Engine

Purpose: compare current and historical posture mechanically. For CDN Insights
this often supports incident and performance triage. For Bot Insights,
especially in Akamai contexts, this should primarily support broader bot
program health, movement, and policy effectiveness analysis rather than
competing with real-time bot mitigation workflows.

Useful methods:

- Current window versus immediately previous window.
- Month over month.
- Quarter over quarter.
- Year over year or same week last year.
- Current window versus same hour yesterday.
- Current window versus same hour last week.
- Rolling median plus median absolute deviation.
- EWMA or CUSUM for sustained shifts.
- Low-count-aware rate tests for sparse bot slices.

Candidate metrics:

- CDN: requests, bytes, 4xx rate, 5xx rate, cache hit rate, cache miss rate,
  origin p95, edge p95, POP error rate, CDN traffic mix.
- Bot: bot share, bad bot share, suspicious bot share, verified crawler error
  rate, 429 rate, attack-data rate, AI crawler share, query-string diversity,
  bot-origin cost.

Output shape:

```json
{
  "metric": "cache_miss_pct",
  "scope": {"request_host": "www.example.com"},
  "current_window": "2026-04-18T12:00:00Z/2026-04-18T18:00:00Z",
  "baseline_window": "2026-04-11T12:00:00Z/2026-04-11T18:00:00Z",
  "current": 18.4,
  "baseline": 7.2,
  "absolute_delta": 11.2,
  "pct_change": 155.56,
  "anomaly_score": 4.8,
  "severity": "high",
  "method": "seasonal_hour_of_week_median",
  "confidence": "high"
}
```

Bot posture movement output should use the same mechanical fields, but with
language that reflects movement rather than incident judgment:

```json
{
  "schema_version": "bot_posture_movement.v1",
  "comparison_type": "quarter_over_quarter",
  "granularity": "day",
  "table_used": "bot_summary_day",
  "scope": {"request_host": "www.example.com"},
  "current_window": {"start": "2026-01-01", "end": "2026-04-01"},
  "baseline_windows": [
    {"start": "2025-10-01", "end": "2026-01-01", "label": "previous_quarter"}
  ],
  "metrics": [
    {
      "name": "bot_share_pct",
      "current": 34.2,
      "baseline": 29.7,
      "absolute_delta": 4.5,
      "pct_change": 15.15,
      "direction": "increase",
      "confidence": "high",
      "confidence_reasons": ["summary_table_used", "baseline_count_sufficient"]
    }
  ],
  "interpretation_constraints": [
    "movement_only",
    "no_causal_claim",
    "llm_may_summarize_structured_evidence_only"
  ]
}
```

### 2. Mover Attribution

Purpose: identify dimensions that explain the observed change.

Candidate dimensions:

- CDN: host, path, CDN, status code, country, city, ASN, POP, cache state,
  user-agent category.
- Bot: all CDN dimensions plus bot class, confidence, intent, category, verified
  owner, verification tier, AI category, ASN type.

Useful outputs:

- Top contributors by absolute delta.
- Top contributors by percentage delta with minimum-volume guard.
- Contribution share of the total change.
- Newly seen entities.
- Disappearing entities.
- Directional movers: increasing, decreasing, or displaced.

Output shape:

```json
{
  "metric": "requests",
  "dimension": "client_asn",
  "total_delta": 120000,
  "movers": [
    {
      "value": "12345",
      "current": 64000,
      "baseline": 9000,
      "absolute_delta": 55000,
      "pct_change": 611.11,
      "contribution_pct": 45.83,
      "confidence": "high"
    }
  ],
  "method": "absolute_delta_rank"
}
```

### 3. Forecasting

Purpose: estimate expected near-future ranges for operational metrics.

Good initial forecast targets:

- Request volume.
- Bandwidth.
- Cache miss volume.
- Origin request volume.
- 5xx count.
- Bot request volume.
- Verified crawler volume.
- AI crawler volume.
- Origin p95 or p99 latency, with caution.

Recommended first methods:

- Seasonal naive forecast from matching historical hour/day buckets.
- Moving average by hour-of-week.
- Exponential smoothing if `statsmodels` is introduced.
- Backtesting with MAPE, SMAPE, or MAE.

Output shape:

```json
{
  "metric": "requests",
  "forecast_window": "next_6h",
  "expected": 12400000,
  "lower": 9800000,
  "upper": 15300000,
  "method": "seasonal_hour_of_week_median",
  "backtest_mape_pct": 8.7,
  "confidence": "medium"
}
```

### 4. Bot Risk Scorecards

Purpose: produce explainable, rule-based risk evidence for bot entities without
letting the LLM classify traffic directly.

Candidate features:

- `bot_confidence = suspicious`.
- `bot_class = bad`.
- Residential or otherwise unexpected `asn_type`.
- High query-string diversity.
- High unique IP count for an ASN/path.
- Low cache hit rate.
- High 403, 429, or 5xx rate.
- Attack data present.
- Crawler user agent without verification tier.
- Sudden ASN/path volume delta.

Output shape:

```json
{
  "entity_type": "client_asn",
  "entity": "12345",
  "risk_score": 82,
  "risk_band": "high",
  "features": [
    {"name": "suspicious_confidence", "points": 20},
    {"name": "high_querystring_diversity", "points": 15},
    {"name": "volume_delta", "points": 18}
  ],
  "method": "rule_based_scorecard",
  "classification_source": "mechanical_features_only"
}
```

### 5. Good Bot and SEO Health Monitoring

Purpose: detect crawler disruptions and governance issues that can affect search
visibility or partner integrations.

Candidate checks:

- Verified crawler volume drop versus baseline.
- Verified crawler 403, 429, or 5xx spike.
- Important paths not crawled recently.
- `robots.txt`, `llms.txt`, and sitemap access failures.
- AI crawler growth by owner and category.
- Crawler cache miss rate and origin cost.
- Verified owner appearing from unexpected ASNs or countries.

Output shape:

```json
{
  "finding_type": "verified_crawler_disruption",
  "verified_bot_owner": "Google",
  "metric": "rate_429_pct",
  "current": 6.4,
  "baseline": 0.2,
  "affected_paths": ["/robots.txt", "/products"],
  "severity": "high",
  "method": "current_vs_seasonal_baseline"
}
```

### 6. Cache-Busting and Origin-Impact Detection

Purpose: identify bot or client behavior that defeats caching and increases
origin load.

Candidate detectors:

- Query-string diversity ratio by path and ASN.
- Cache miss delta by path.
- Origin p95 or p99 delta by path.
- Origin cost score: requests multiplied by origin p95.
- Bot-attributable cache misses.
- Bytes served to bots by class, owner, or category.

Output shape:

```json
{
  "finding_type": "cache_busting_candidate",
  "request_path": "/api/search",
  "client_asn": "12345",
  "requests": 82000,
  "unique_query_strings": 79700,
  "qs_diversity_ratio": 0.971,
  "miss_rate_pct": 94.2,
  "bot_share_pct": 99.1,
  "method": "ratio_threshold_with_volume_guard"
}
```

### 7. Incident Timeline Reconstruction

Purpose: turn an anomaly into a mechanically derived timeline.

Candidate outputs:

- First anomalous bucket.
- Peak bucket.
- Recovery bucket.
- Before, during, and after metrics.
- Top changing dimensions by phase.
- Whether bot share, cache miss rate, latency, or errors moved first.

This should produce a structured timeline for the LLM to narrate, not a
free-form incident story.

### 8. Correlation and Lead-Lag Analysis

Purpose: identify relationships worth investigating.

Candidate relationships:

- Bot share versus cache miss rate.
- Cache miss rate versus origin p95.
- 5xx rate versus origin p95.
- ASN volume versus 429 rate.
- AI crawler volume versus bandwidth.
- CDN or POP traffic shift versus latency.

Output shape:

```json
{
  "x": "bot_share_pct",
  "y": "cache_miss_pct",
  "correlation": 0.81,
  "lag_minutes": 5,
  "method": "spearman",
  "interpretation_constraint": "correlation_only"
}
```

### 9. Capacity and SLO Burn Analysis

Purpose: quantify current and projected risk against operational thresholds.

Candidate checks:

- 5xx error-budget burn.
- Latency SLO burn.
- Origin request budget burn.
- CDN bandwidth budget forecast.
- Per-host or per-CDN threshold breach risk.

Output shape:

```json
{
  "slo": "origin_p95_ms < 500",
  "current_burn_rate": 3.2,
  "projected_time_to_breach": "4h",
  "top_contributors": [
    {"dimension": "request_path", "value": "/api/search", "contribution_pct": 37.4}
  ],
  "method": "threshold_burn_rate"
}
```

### 10. Mitigation, Policy, and Control Verification

Purpose: verify whether a mitigation, policy change, or managed bot-control
change improved the target metric and whether it created collateral damage.
For Bot Insights this should be framed as control effectiveness or posture
review unless the user is explicitly investigating an active incident.

Inputs:

- Mitigation timestamp.
- Target dimensions, such as ASN, host, path, bot class, CDN, or rule.
- Metrics to evaluate.

Candidate outputs:

- Before and after deltas.
- Confidence based on sample size.
- Target effect.
- Collateral impact on good bots, verified owners, cache hit rate, 4xx/429, and
  5xx.
- Displacement to other ASNs, paths, countries, or CDNs.

Output shape:

```json
{
  "mitigation_time": "2026-04-18T14:00:00Z",
  "target": {"client_asn": "12345"},
  "target_effect": {
    "metric": "requests",
    "before": 88000,
    "after": 12000,
    "pct_change": -86.36
  },
  "collateral_checks": [
    {
      "metric": "verified_good_bot_429_rate",
      "before": 0.1,
      "after": 0.1,
      "status": "unchanged"
    }
  ],
  "displacement_checks": [
    {
      "dimension": "client_asn",
      "new_movers_detected": 2,
      "status": "review"
    }
  ],
  "method": "before_after_with_collateral_checks"
}
```

## Suggested Implementation Shape

Use a small mechanical analytics package behind the skills rather than adding
large free-form instructions directly to the LLM-facing skill docs.

Candidate files:

- `metric_registry.yaml`: canonical metric definitions, SQL expressions, units,
  and allowed tables.
- `dimensions.yaml`: attribution dimensions by table and summary-table support.
- `baselines.py`: current/baseline deltas and robust anomaly scores.
- `forecast.py`: seasonal naive forecasts and optional smoothing methods.
- `attribution.py`: top movers, contribution math, new/disappearing entities.
- `risk_score.py`: transparent scorecards for bot and cache-busting candidates.
- `report_schema.json`: stable structured output consumed by the LLM.

The skills can then reference the mechanical toolkit and describe when to use
each function, while the function outputs remain deterministic and testable.

Do not add database clients, connection configuration, credential handling, or
direct Hydrolix query execution to these scripts. All Hydrolix database access
should go through the Hydrolix MCP server or the host agent's existing Hydrolix
query tool. Local scripts should accept Hydrolix MCP query results, saved JSON,
or pasted aggregate JSON and emit deterministic structured analytics.

Layer responsibilities:

- Hydrolix MCP: table discovery, summary metadata inspection, SQL execution,
  summary-table merge semantics, and query guardrails.
- SQL: timestamp filtering, period aggregation, candidate baseline windows,
  metric numerators/denominators, and dimension group-bys.
- Local scripts: baseline selection metadata, delta math, contribution math,
  confidence labels, status labels, and schema-shaped JSON.
- LLM: interpretation, caveats, and follow-up questions from structured
  outputs only.

## First Implementation Candidates

Start with features that require little or no dependency footprint and map
directly to existing skill scope:

1. Baseline, posture movement, and anomaly engine for CDN and bot metrics.
2. Mover attribution by host, path, ASN, CDN, status, bot class, and bot owner.
3. Cache-busting and origin-impact detector.
4. Verified crawler and AI crawler health monitor.
5. Policy, mitigation, and control-effectiveness review with collateral-impact
   checks.

These provide high value while preserving explainability.

## Akamai Feedback Areas

Use this section to capture specific feedback from Akamai and map it to
mechanical functions.

### Bot Insights Limited Availability: Posture Movement and Health

Akamai has its own bot management and real-time mitigation capabilities. The
Bot Insights skill should therefore emphasize broader analysis, general health,
and program movement over immediate incident mitigation. Short-window
comparisons remain useful, but they should not be the default framing for the
limited availability release.

Primary decisions supported:

- How did bot posture move quarter over quarter, month over month, week over
  week, or year over year?
- Which hosts, ASNs, bot classes, resource categories, AI categories, policies,
  or actions explain the movement?
- Are good bots, verified crawlers, and AI crawlers healthy over time?
- Are bot-control policies changing outcomes without unacceptable collateral
  impact?
- Are Akamai bot signals and Hydrolix-derived signals aligned over time?
- Are cache, origin, error, or bandwidth costs increasingly bot-attributable?

Initial baseline priority for Bot Insights:

1. `quarter_over_quarter`
2. `month_over_month`
3. `week_over_week`
4. `year_over_year` or `same_week_last_year`
5. `same_weekday_hour_last_week`
6. `same_hour_yesterday`
7. `previous_window`
8. `explicit_before_after`
9. `post_change_vs_expected`

Daily summaries should be the default query surface for quarter-over-quarter,
month-over-month, year-over-year, and executive posture movement. Hourly
summaries should be used when hour-of-day or weekday/hour seasonality matters.
Minute summaries should be reserved for short policy-change reviews, detailed
timelines, or incident-style investigations.

Do not add coarser monthly or quarterly summaries preemptively. QoQ movement
should first be benchmarked against existing daily summaries.
Add coarser summaries only when measured query latency, scanned rows, memory
usage, cost, or retention behavior shows that daily summaries are insufficient.

Representative benchmark queries:

- Overall posture quarter over quarter by `request_host`.
- Bot share quarter over quarter by `request_host`, `is_bot_traffic`, and
  `bot_class`.
- AI crawler movement quarter over quarter by `ai_category`.
- ASN mover attribution quarter over quarter using `bot_agg_asn_hour` or a
  daily ASN summary if available.
- Path and resource movement quarter over quarter using `bot_agg_path_day` and
  `bot_agg_resource_day`.
- SIEM action and policy movement quarter over quarter using
  `bot_siem_summary_day`.

Existing summary dimensions that should be treated as first-class for the
limited availability release:

- `request_host`
- `hdx_cdn`
- `bot_class`
- `is_bot_traffic`
- `ai_category`
- `client_asn`
- `asn_type`
- `resource_category`
- `request_method`
- `request_path_norm`
- `action_taken`
- `policy_id`
- `akamai_canonical_bot_class`

Likely summary expansion candidates are dimensions that support posture and
health questions but are not fully covered by the current summary surface:

- `verified_bot_owner`
- `bot_confidence`
- `bot_intent`
- `bot_category`
- `bot_type`
- `client_country_iso_code`
- `edge_pop`
- exact or normalized `response_status_code`
- attack-data presence or parsed attack category
- `user_agent_category`

The immediate schema investment should favor missing posture dimensions at
hour/day granularity over coarser rollups of already-covered dimensions. New
coarser summaries should be justified by benchmark evidence or retention
requirements, not by assumption.

Initial Bot Insights posture metrics:

- `requests`
- `bot_share_pct`
- `human_share_pct`
- `good_bot_share_pct`
- `bad_bot_share_pct`
- `unknown_bot_share_pct`
- `ai_crawler_share_pct`
- `rate_4xx_pct`
- `rate_429_pct`
- `rate_5xx_pct`
- `cache_miss_pct`
- `origin_p95_ms`
- `origin_p99_ms`
- `unique_client_ips`
- `unique_paths`
- `unique_query_strings`
- `siem_blocked_requests`
- `siem_auth_fail_requests`
- `siem_business_fail_requests`
- `avg_bot_score`

Structured output for posture movement:

```json
{
  "schema_version": "bot_posture_movement.v1",
  "comparison_type": "quarter_over_quarter",
  "granularity": "day",
  "table_used": "bot_summary_day",
  "scope": {"request_host": "www.example.com"},
  "current_window": {"start": "2026-01-01", "end": "2026-04-01"},
  "baseline_windows": [
    {"start": "2025-10-01", "end": "2026-01-01", "label": "previous_quarter"}
  ],
  "metrics": [
    {
      "name": "bot_share_pct",
      "unit": "percent",
      "current": 34.2,
      "baseline": 29.7,
      "absolute_delta": 4.5,
      "pct_change": 15.15,
      "direction": "increase",
      "confidence": "high",
      "confidence_reasons": ["summary_table_used", "baseline_count_sufficient"]
    }
  ],
  "movers": [
    {
      "dimension": "client_asn",
      "value": "12345",
      "metric": "requests",
      "current": 12400000,
      "baseline": 4200000,
      "absolute_delta": 8200000,
      "pct_change": 195.24,
      "contribution_pct": 22.4,
      "confidence": "high"
    }
  ],
  "interpretation_constraints": [
    "movement_only",
    "no_causal_claim",
    "llm_may_summarize_structured_evidence_only"
  ]
}
```

Structured output for policy or control review:

```json
{
  "schema_version": "bot_control_review.v1",
  "comparison_type": "post_change_vs_expected",
  "change_time": "2026-04-01T00:00:00Z",
  "target": {"policy_id": "policy-123"},
  "table_used": "bot_siem_summary_day",
  "target_effects": [
    {
      "metric": "siem_blocked_requests",
      "before": 880000,
      "after": 1200000,
      "expected": 910000,
      "absolute_delta_vs_expected": 290000,
      "pct_change_vs_expected": 31.87,
      "status": "increased",
      "confidence": "high"
    }
  ],
  "collateral_checks": [
    {
      "metric": "rate_5xx_pct",
      "before": 0.4,
      "after": 0.5,
      "status": "review"
    }
  ],
  "displacement_checks": [
    {
      "dimension": "client_asn",
      "new_or_increased_movers": 2,
      "status": "review"
    }
  ],
  "interpretation_constraints": [
    "control_effectiveness_review",
    "no_causal_claim_without_external_change_evidence"
  ]
}
```

Confidence should be label-plus-reasons rather than opaque statistics:

- `high`: summary table used, comparable windows available, current and
  baseline counts meet metric-specific minimums, and baseline granularity
  matches the comparison type.
- `medium`: summary table used but only one comparable historical window exists,
  a fallback baseline was selected, or one source coverage caveat applies.
- `low`: sparse counts, partial current bucket, raw-table fallback, missing
  retained dimension, or source-specific enrichment caveats materially affect
  the metric.

This confidence model does not require external dependencies. QoQ, MoM, WoW,
simple seasonal medians, mover attribution, contribution math, and policy-review
deltas should stay in the Python standard library plus SQL tier unless
benchmarks or validation needs justify a dependency.

Bot Insights integrated analytics should not include a database client. Query
execution should always prefer the Hydrolix MCP server, with local scripts
operating only on aggregate JSON produced by MCP-backed SQL queries.

Specific public-skill documentation edits:

- Update `skills/bot-insights/SKILL.md` and
  `skills/bot-insights/references/data-model.md` to document available summary
  tables instead of describing Bot Insights as request-table-only.
- Add `skills/bot-insights/references/summary-tables.md` covering retained
  dimensions and metric support by summary table.
- Add `skills/bot-insights/references/baseline-comparison.md` covering posture
  movement, baseline selection, confidence, and output schemas.
- Update SOC and executive references so adjacent-window incident comparisons
  remain available but no longer dominate the bot-insights baseline story.
- Update pitfalls to say summary tables should be preferred when dimensions fit,
  and raw-table fallback is required for dimensions not retained in summaries.

For each feedback item, document:

- The user or persona affected.
- The operational decision it supports.
- Required fields and data sources.
- Whether it belongs in CDN Insights, Bot Insights, or both.
- The mechanical method.
- The structured output schema.
- Any dependency justification.
- Known caveats or confidence limits.

### Feedback Item Template

```text
Name:
Persona:
Decision supported:
Skill scope:
Required fields:
Mechanical method:
Output:
Dependency needed:
Caveats:
Priority:
```
