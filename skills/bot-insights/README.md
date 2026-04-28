# Bot Insights Analysis README

This README describes what the Bot Insights analyses are, how the main
measurements are calculated, and how to interpret the results. The reference
files in `references/` remain the runbooks for query templates and execution
patterns.

Bot Insights combines CDN access logs with bot and security enrichment so an
analyst can connect automation identity to operational impact: who the traffic
appears to be, whether that claim is credible, what the traffic is doing, and
whether it is affecting cache, origin, SEO, or security posture.

## How To Use These Docs

This README is the human-facing manual for Bot Insights analytics. It explains
what each analysis is for, the main formulas, what the outputs mean, and where
to go next.

`SKILL.md` is the agent routing file. It intentionally stays shorter and points
agents to the smallest relevant reference instead of loading every schema,
query pattern, and example at once. The detailed runbooks in `references/`
remain the source of truth for SQL templates, input contracts, output schemas,
confidence rules, and renderer behavior.

If you are deciding which analysis to run, start with the catalog below. If you
are building or validating a specific artifact, open the linked reference for
that artifact family.

## Overview

The Bot Insights tooling is not limited to the analyses listed below. Analysts
can use the same summary tables, request-level fallback rules, and Hydrolix MCP
workflow to answer other bot-related questions when the retained dimensions and
source coverage fit the question.

The analyses in this README are the current set with clear deterministic
support in the skill: documented summary-first query patterns, stable formulas,
local script output schemas, scorecard rules, or explicit interpretation
constraints. Treat other analyses as exploratory unless they are reduced to the
same kind of aggregate evidence and documented calculation rules.

| Analysis | Details | Deterministic support | Best fit |
|----------|---------|-----------------------|----------|
| Posture movement | [Posture Movement](#posture-movement) | Current/baseline formulas, `bot_posture_movement.v1`, confidence reasons | What changed for a scope over a comparable baseline. |
| Mover attribution | [Mover Attribution](#mover-attribution) | Single-dimension current/baseline delta and contribution math, `bot_mover_attribution.v1` | Which ASN, host, path, class, category, policy, or action drove movement. |
| Control review | [Control Review](#control-review) | Before/after or after/expected formulas, `bot_control_review.v1` | Whether a known policy or control change behaved as expected. |
| Policy collateral and displacement | [Policy Collateral And Displacement](#policy-collateral-and-displacement) | Collateral/displacement checks plus `policy_collateral` scorecard features | Whether a policy change affected protected traffic or shifted traffic elsewhere. |
| SOC and security | [SOC And Security Analysis](#soc-and-security-analysis) | Summary-backed movement, SIEM metrics, raw fallback rules for exact evidence | Suspicious automation, spoofing, attack evidence, and incident follow-up. |
| SEO and crawler governance | [SEO And Crawler Governance](#seo-and-crawler-governance) | Crawler health formulas, good bot and AI crawler summary patterns, raw fallback rules for owner verification | Good crawler availability, AI crawler monitoring, and governance surfaces. |
| Edge and operations | [Edge And Operations Analysis](#edge-and-operations-analysis) | Cache, query-string diversity, origin latency, and origin cost proxy formulas | Cache busting, origin pressure, latency, and byte/cost investigations. |
| Cache-origin impact detector | [Cache-Origin Impact Detector](#cache-origin-impact-detector) | Path-grain candidate scoring, `cache_origin_impact_report.v1`, explicit confidence and limitations | Structured cache-busting, cache-miss movement, origin-pressure, and bot-attributable cache impact reports. |
| Executive posture and team routing | [Executive Posture And Team Routing](#executive-posture-and-team-routing) | Day-summary posture formulas and comparable-window guidance | Cross-domain posture, prioritization, and routing to SOC, SEO, or Edge/Ops. |
| Entity scorecards | [Scorecards](#scorecards) | Rule-based feature thresholds, score bands, `bot_entity_scorecard.v1`, `bot_scorecard_index.v1` | Reusable entity prioritization and handoff packets. |

## Analysis Boundary

Hydrolix does the heavy work: filtering, grouping, table-specific merge
functions, and aggregation over large row sets. The local scripts in `scripts/`
only process small aggregate JSON outputs from Hydrolix MCP results, saved JSON,
or pasted JSON. They do not query Hydrolix, open database clients, read
credentials, or classify raw request traffic.

Prefer summary tables whenever their retained dimensions answer the question.
Use request-level fallback only for fields not retained in summaries, such as
exact user agent, exact query string, verified bot owner, bot confidence,
verification tier, country, edge POP, attack payload details, or exact status
code inspection.

## Data Surfaces

| Surface | Main tables | What it is for |
|---------|-------------|----------------|
| Request-level bot records | `bot_detection` | Exact request inspection and fields not retained by summaries. |
| Request-level SIEM records | `bot_detection_siem` | Akamai SIEM request inspection and SIEM-specific enrichment. |
| General posture summaries | `bot_summary_minute`, `bot_summary_hour`, `bot_summary_day` | Bot share, class, AI category, ASN, host, CDN, resource, method, cache, errors, and origin latency. |
| Focused aggregate summaries | `bot_agg_*` | Host, ASN, path, resource, traffic, and bot-class drilldowns. |
| SIEM summaries | `bot_siem_*` | Action, policy, blocked requests, auth failures, Akamai canonical class, and filter-aware SIEM views. |

Granularity is part of the meaning:

- Day summaries are the default for executive posture, quarter-over-quarter,
  month-over-month, year-over-year, and same-week-last-year comparisons.
- Hour summaries are the default for same-hour-yesterday,
  same-weekday-hour-last-week, daily rhythm, and weekday/hour seasonality.
- Minute summaries are for short control reviews, incident timelines, and
  detailed policy-change follow-up.

## Analysis Catalog

### Posture Movement

Posture movement answers "what changed?" for a scope such as host, CDN, ASN,
bot class, AI category, path, resource category, or method.

Typical metrics:

- request volume
- bot share
- good bot share
- bad bot share
- AI crawler share
- 429 rate
- 5xx rate
- cache miss rate
- origin p95 latency

How it is calculated:

1. Query a current window and one or more baseline windows from summary tables.
2. Aggregate each metric for the current and baseline periods.
3. Compute:

```text
absolute_delta = current - baseline
pct_change = (current - baseline) / greatest(baseline, 1) * 100
direction = increase | decrease | no_change
```

What it means:

Posture movement is a comparison, not a causal claim. An increase in bad bot
share, cache misses, or 429s says that the measured posture moved for the
selected scope. It does not, by itself, prove why it moved.

Script support:

- `scripts/compare_delta.py` emits simple current/baseline numeric deltas.
- `scripts/compare_posture.py --schema posture` emits
  `bot_posture_movement.v1` with confidence reasons and interpretation guards.

### Mover Attribution

Mover attribution answers "who or what drove the change?" after posture movement
shows that a metric changed.

Supported retained dimensions include:

- `client_asn`
- `request_host`
- `bot_class`
- `ai_category`
- `request_path_norm`
- `resource_category`
- `request_method`
- SIEM dimensions such as `policy_id`, `action_taken`, and
  `akamai_canonical_bot_class` when SIEM summaries are used

How it is calculated:

```text
entity_delta = entity_current - entity_baseline
pct_change = entity_delta / greatest(entity_baseline, 1) * 100
contribution_pct = abs(entity_delta) / sum(abs(entity_delta) for compared movers) * 100
```

When a complete-scope denominator is available, contribution means "this entity
accounts for this percentage of the total absolute movement." If the input was
limited before the denominator was calculated, contribution is only over the
returned movers and should not be treated as whole-scope attribution.

What it means:

Mover attribution prioritizes follow-up. A top mover is the entity whose
aggregate value changed most, not necessarily the entity with the largest total
volume.

Script support:

- `scripts/compare_posture.py --schema movers` emits
  `bot_mover_attribution.v1`.

### Control Review

Control review answers "did a known policy or control change have the intended
effect, and were there collateral changes?"

Common targets:

- SIEM blocked requests
- SIEM auth failures
- SIEM business failures
- 5xx rate
- cache miss rate
- good crawler 429s or errors
- displacement to another host, ASN, path, or bot class

How it is calculated:

The script compares `after` to `expected`. If an explicit expected value or
expected window is not supplied, `before` is used as the expected value.

```text
absolute_delta_vs_expected = after - expected
pct_change_vs_expected = (after - expected) / greatest(expected, 1) * 100
status = within_expected | increased | decreased | improved | review
```

By default, values within 5 percent of expected are `within_expected`.
If a desired direction is supplied and the observed direction matches it, the
status is `improved`; otherwise the status is `review`.

What it means:

Control review is an effectiveness check. It should only be framed as causality
when the analyst also has external evidence that the reviewed control change
occurred at the stated time and was the relevant change.

Script support:

- `scripts/compare_posture.py --schema control` emits
  `bot_control_review.v1`.

### Policy Collateral And Displacement

Policy collateral analysis answers "did the policy change create side effects
or shift traffic elsewhere?" It extends control review beyond the intended
target metric.

Common checks:

- good bot 429s or 5xx after a block or rate-limit change
- governance-surface failures for `robots.txt`, `llms.txt`, or sitemaps
- cache miss rate or origin p95 after a cache-key or bot-control change
- displacement to another host, path, ASN, bot class, CDN source, SIEM policy,
  or action outcome

What it means:

Target effects are not enough to declare success. A policy can move the target
metric while increasing protected-traffic errors or shifting traffic to a
different retained segment. Treat collateral and displacement results as a
review queue unless external change evidence and follow-up investigation
support a stronger conclusion.

Script support:

- `scripts/compare_posture.py --schema control` preserves
  `collateral_checks` and `displacement_checks`.
- `scripts/attribution.py --analysis policy_displacement` emits
  `bot_attribution_report.v1` with a positive/negative displacement summary for
  retained dimension aggregates.
- `scripts/scorecard.py` scores policy collateral fields in the
  `policy_collateral` domain for ranked follow-up.

### SOC And Security Analysis

SOC analysis focuses on suspicious automation, attack evidence, signal
alignment, spoofing, and incident follow-up.

Available analyses:

- short-window posture movement
- mover attribution by ASN, path, host, bot class, AI category, action, or
  policy
- newly seen ASNs, IPs, user agents, or other request-level entities
- behavioral fingerprints by status code, method, and endpoint concentration
- bot score distribution
- spoof detection for crawlers claiming known identities
- Akamai classification versus Hydrolix-derived classification alignment
- attack data presence and SIEM action evidence

How it is calculated:

- Summary-backed posture uses `cnt_all`, `cnt_429`, `cnt_5xx`,
  `cnt_cache_miss`, `is_bot_traffic`, `bot_class`, and retained dimensions.
- Score distributions and exact spoof evidence use request-level fallback
  because `bot_confidence`, exact `user_agent`, `verified_bot_owner`, and exact
  attack payload details are not retained in the current summary catalog.
- SIEM evidence uses `cnt_blocked`, `cnt_auth_fail`, `cnt_biz_fail`,
  `action_taken`, and `policy_id` from SIEM summaries when possible.

What it means:

No single signal should be treated as proof of malicious behavior. Stronger
security conclusions require multiple pieces of evidence, such as source
network, classification divergence, SIEM action, attack data, request pattern,
and operational impact.

### SEO And Crawler Governance

SEO analysis focuses on legitimate crawler health, governance files, AI crawler
monitoring, and accidental blocking.

Available analyses:

- verified versus unverified bot owners
- good bot health by host and day
- good crawler 429 and 5xx exposure
- good bot access patterns by normalized path
- good bot volume drops
- AI crawler movement by `ai_category`
- AI crawler access to governance surfaces such as `robots.txt` and `llms.txt`

How it is calculated:

```text
good_bot_error_rate_pct = good_bot_4xx_5xx_requests / greatest(good_bot_requests, 1) * 100
rate_limited_pct = cnt_429 / greatest(cnt_all, 1) * 100
ai_crawler_share_pct = requests where ai_category != '' / greatest(total_requests, 1) * 100
```

Owner-specific crawler health requires request-level fallback because
`verified_bot_owner` and `bot_verification_tier` are not retained in the current
summary tables.

What it means:

SEO findings are governance and availability signals. A spike in 429s for good
bots may mean intentional throttling, accidental collateral damage, or upstream
origin distress. Treat it as a triage signal and check the related policy and
origin context.

### Edge And Operations Analysis

Edge/Ops analysis focuses on cache efficiency, origin load, query-string churn,
bandwidth cost, and bot-driven operational pressure.

Available analyses:

- cache impact by bot traffic and bot class
- query-string diversity by normalized path
- query-string churn by ASN and path
- origin latency by bot class
- origin cost proxy by path
- byte-level cost attribution by bot class

How it is calculated:

```text
cache_miss_pct = sum(cnt_cache_miss) / greatest(sum(cnt_all), 1) * 100
hit_rate_pct = sum(cnt_cached) / greatest(sum(cnt_all), 1) * 100
qs_diversity_ratio = unique_query_strings / greatest(requests, 1)
origin_cost_score = requests * origin_p95_ms
origin_cost_contribution_pct = entity_origin_cost_score / greatest(sum(origin_cost_score), 1) * 100
```

Byte-level cost attribution uses request-level fallback because response bytes
are not in the current summary metrics.

What it means:

High query-string diversity with a high miss rate is evidence of possible cache
busting or cache-key mismatch. High origin cost proxy identifies where volume
and origin latency combine into operational pressure; it is not a billing
measure unless separately tied to cost data.

Script support:

- `scripts/cache_origin_impact.py` emits deterministic
  `cache_origin_impact_report.v1` reports from already-aggregated path-grain
  rows when a structured cache-busting or origin-impact packet is needed.

### Cache-Origin Impact Detector

The cache-origin impact detector is the structured Edge/Ops path for
cache-busting, query-string churn, cache-miss movement, origin-pressure, and
bot-attributable cache impact questions.

Supported v1 surfaces:

- `bot_agg_path_day`
- `bot_agg_path_hour`
- `bot_agg_path_minute`

Supported v1 dimensions:

- `request_host + request_path_norm`
- `request_host + request_path_norm + bot_class`
- `request_host + request_path_norm + asn_type`
- `request_host + request_path_norm + bot_class + asn_type`

The detector normalizes aggregate rows into canonical current, baseline, and
delta fields, then emits one ranked candidate list. Common finding types are:

- `cache_busting_candidate`
- `cache_miss_movement_candidate`
- `origin_impact_candidate`
- `bot_attributable_cache_misses`
- `bot_attributable_origin_pressure`

Key metrics include:

```text
miss_rate_pct = cache_misses / greatest(requests, 1) * 100
qs_diversity_ratio = unique_query_strings / greatest(requests, 1)
origin_pressure_score = cache_misses * max(origin_p95_ms, 1) / 1000
cache_miss_contribution_pct = candidate_cache_misses / complete_scope_current_cache_misses * 100
```

The detector can duration-normalize additive baseline metrics when current and
baseline windows have unequal durations. It does not duration-normalize
tail-latency values or period-level unique query-string counts.

Inputs must be aggregate JSON from Hydrolix MCP results, saved JSON, pasted
JSON, or a reviewed wrapper. The local script does not query Hydrolix, read
credentials, prove causality, classify traffic with opaque models, or recommend
mitigations.

Confidence is capped at `medium` for file, stdin, pasted, saved MCP-shaped, or
ordinary caller-supplied JSON. `high` confidence is reserved for a reviewed
in-process wrapper that passes direct query provenance, table metadata,
retained-dimension proof, comparable-window evidence, support counts, and
complete-scope contribution evidence.

Missing optional metric inputs are reported in `not_evaluated`, not treated as
zero or safe evidence. Contribution percentages require complete-scope
denominators computed before row limits, or trusted precomputed percentages
with basis metadata. Source-limited contribution fields are withheld and
reported explicitly.

Script support:

- `scripts/cache_origin_impact.py --file cache-origin-input.json` emits
  `cache_origin_impact_report.v1`.
- `references/cache-origin-impact.md` contains the full input contract, output
  shape, confidence boundary, SQL template guidance, and examples.

### Executive Posture And Team Routing

Executive analysis condenses bot posture into cross-domain health and routing
signals.

Available analyses:

- month-over-month, quarter-over-quarter, week-over-week, or year-over-year
  posture by host
- multi-domain triage by request volume, bot share, 429 rate, 5xx rate, and
  cache miss rate
- post-mitigation or post-policy review
- team routing across SOC, SEO, and Edge/Ops concerns

How it is calculated:

Executive views use the same summary metrics and delta formulas as posture
movement, usually from day summaries. They emphasize comparable windows and
stable scope rather than incident-level detail.

What it means:

Executive posture answers where attention should go first. It should avoid
over-specific root cause statements until SOC, SEO, or Edge/Ops drilldowns
confirm the evidence.

## Metric Reference

| Metric | Calculation | Meaning |
|--------|-------------|---------|
| `requests` | `sum(cnt_all)` or request-level `count()` | Total request volume for the selected scope. |
| `bot_share_pct` | `sumIf(cnt_all, is_bot_traffic = true) / greatest(sum(cnt_all), 1) * 100` | Percent of traffic classified as bot traffic. |
| `good_bot_share_pct` | `sumIf(cnt_all, bot_class = 'good') / greatest(sum(cnt_all), 1) * 100` | Percent of traffic classified as good bot traffic. |
| `bad_bot_share_pct` | `sumIf(cnt_all, bot_class = 'bad') / greatest(sum(cnt_all), 1) * 100` | Percent of traffic classified as bad bot traffic. |
| `ai_crawler_share_pct` | `sumIf(cnt_all, ai_category != '') / greatest(sum(cnt_all), 1) * 100` | Percent of traffic associated with AI crawler categories. |
| `rate_429_pct` | `sum(cnt_429) / greatest(sum(cnt_all), 1) * 100` | Rate-limited request share. |
| `rate_5xx_pct` | `sum(cnt_5xx) / greatest(sum(cnt_all), 1) * 100` | Server error share. |
| `cache_miss_pct` | `sum(cnt_cache_miss) / greatest(sum(cnt_all), 1) * 100` | Share of requests that missed cache. |
| `hit_rate_pct` | `sum(cnt_cached) / greatest(sum(cnt_all), 1) * 100` | Share of requests served from cache. |
| `avg_origin_ttfb` | Summary average origin TTFB | Average origin latency for the selected grouping. |
| `origin_p95_ms` | `max(p95_origin_ttfb)` in current templates | Tail origin latency proxy for the selected grouping. |
| `qs_diversity_ratio` | `uniq_qs / greatest(requests, 1)` | How many distinct query strings are seen per request. Values near 1 indicate high churn. |
| `origin_pressure_score` | `cache_misses * max(origin_p95_ms, 1) / 1000` | Cache-origin detector proxy combining miss volume and tail origin latency. Not a billing or capacity unit. |
| `cache_miss_contribution_pct` | Candidate misses divided by complete-scope current misses * 100 | Whole-scope contribution only when denominator evidence was computed before row limits. |
| `bot_miss_share_pct` | Selected bot-class misses for a path divided by all misses for that path * 100 | Bot-attributable cache-miss share for retained path-grain bot-class evidence. |
| `bot_origin_pressure_share_pct` | Selected bot-class origin pressure for a path divided by total origin pressure for that path * 100 | Bot-attributable share of the detector's proxy origin pressure score. |
| `origin_cost_score` | `requests * origin_p95_ms` | Operational pressure proxy combining volume and tail origin latency. |
| `total_bytes` | `sum(response_total_bytes)` from request-level fallback | Byte volume. Not available in current summaries. |

## Baseline Methods

| Method | Baseline | Default granularity | Main use |
|--------|----------|---------------------|----------|
| `quarter_over_quarter` | Previous complete quarter | day | Executive posture and program movement. |
| `month_over_month` | Previous complete month | day | Monthly health and policy movement. |
| `week_over_week` | Previous complete week | day or hour | Weekly posture and team routing. |
| `year_over_year` | Same period in previous year | day | Seasonal posture movement. |
| `same_week_last_year` | Same ISO week in previous year | day | Retail, launch, or annual-event comparison. |
| `same_weekday_hour_last_week` | Same weekday and hour one week earlier | hour | Weekday/hour seasonality. |
| `same_hour_yesterday` | Same hour one day earlier | hour | Daily rhythm and fresh shifts. |
| `previous_window` | Immediately preceding equal-length window | hour or minute | Short SOC-style comparison. |
| `explicit_before_after` | User-provided before/after windows | minute, hour, or day | Known change review. |
| `post_change_vs_expected` | Expected value or expected window after a change | day, hour, or minute | Control effectiveness and collateral checks. |

## Confidence Labels

Confidence is a label plus machine-readable reasons, not a statistical
probability.

| Label | Typical meaning |
|-------|-----------------|
| `high` | Summary table used, retained dimensions fit, comparable windows exist, support counts are sufficient, and granularity matches the comparison type. |
| `medium` | Summary-backed but one or more caveats apply, such as fallback baseline, source coverage caveat, unavailable SIEM enrichment, or missing optional feature inputs. |
| `low` | Raw fallback, sparse counts, missing retained dimension, partial current bucket, granularity mismatch, or non-comparable windows. |

Common confidence reasons include `summary_table_used`, `raw_table_fallback`,
`retained_dimensions_fit`, `missing_retained_dimension`,
`comparable_windows_available`, `fallback_baseline_selected`,
`granularity_matches_comparison`, `granularity_mismatch`,
`current_count_sufficient`, `baseline_count_sufficient`, `sparse_counts`,
`partial_current_bucket`, `source_coverage_caveat`, `zero_baseline_guard`,
`siem_unavailable`, and `feature_input_missing`.

## Scorecards

Scorecards turn entity-level aggregate rows into deterministic investigation
packets. They are for prioritization and handoff, not dashboards and not
machine-learning risk scores.

Supported entity types:

- `client_asn`
- `request_path_norm`
- `request_host`
- `bot_class`
- `ai_category`

Scored domains:

- `movement`
- `origin_impact`
- `cache_busting`
- `crawler_governance`
- `security_evidence`
- `policy_collateral`

Reserved domains:

- `signal_alignment`

Score calculation:

```text
score = min(100, sum(points for threshold-crossing features))
primary_domain = highest scoring nonzero domain
```

Score bands:

| Score | Band |
|-------|------|
| 80-100 | `urgent_review` |
| 60-79 | `high_review` |
| 40-59 | `medium_review` |
| 20-39 | `low_review` |
| 0-19 | `observe` |

Scorecard rules:

| Feature | Domain | Points | Trigger |
|---------|--------|--------|---------|
| `new_entity` | movement | 12 | Baseline requests are less than 1 and current requests are greater than 0. |
| `volume_delta_high` | movement | 12 | Request delta is at least 100 and percentage change is at least 100%. |
| `contribution_to_total_delta_high` | movement | 10 | `contribution_pct` is at least 20%. |
| `bot_share_delta_high` | movement | 8 | Bot share increases by at least 10 percentage points. |
| `cache_miss_rate_high` | cache_busting | 10 | Current cache miss rate is at least 50%. |
| `cache_miss_delta_high` | cache_busting | 8 | Cache miss rate increases by at least 15 percentage points. |
| `origin_p95_delta_high` | origin_impact | 10 | Origin p95 increases by at least 100 ms and at least 25%. |
| `origin_cost_contribution_high` | origin_impact | 18 | Origin cost contribution is at least 20%. |
| `querystring_diversity_high` | cache_busting | 16 | Query-string diversity ratio is at least 0.5. |
| `querystring_diversity_with_high_miss_rate` | cache_busting | 18 | Query-string diversity ratio is at least 0.5 and cache miss rate is at least 50%. |
| `rate_429_delta_high` | crawler_governance | 8 | 429 rate increases by at least 5 percentage points. |
| `rate_5xx_delta_high` | crawler_governance | 8 | 5xx rate increases by at least 5 percentage points. |
| `good_bot_429_present` | crawler_governance | 14 | Good bot 429 request count is greater than 0. |
| `good_bot_error_rate_high` | crawler_governance | 12 | Good bot error rate is at least 5%. |
| `policy_surface_failure_present` | crawler_governance | 16 | Governance surface failures are greater than 0. |
| `ai_crawler_growth_high` | crawler_governance | 10 | AI crawler metric increases by more than 0 and at least 100%. |
| `siem_blocked_present` | security_evidence | 12 | SIEM blocked requests are greater than 0. |
| `siem_auth_fail_present` | security_evidence | 12 | SIEM auth failures are greater than 0. |
| `bad_bot_share_high` | security_evidence | 14 | Bad bot share is at least 50%. |
| `good_bot_policy_collateral_present` | policy_collateral | 16 | Good bot collateral 429 request count is greater than 0. |
| `policy_collateral_error_rate_high` | policy_collateral | 12 | Policy collateral error rate is at least 5%. |
| `displacement_delta_high` | policy_collateral | 12 | Displacement requests increase by at least 100 and at least 50%. |

Missing scorecard inputs are emitted in `not_evaluated_features`; they are not
scored as safe, zero-risk, or zero-impact. Contribution percentages are only
auto-computed by `scorecard.py` when input metadata proves a complete scope with
`rowset_complete: true` or `contribution_basis: "complete_scope"`.

Script support:

- `scripts/scorecard.py` emits `bot_entity_scorecard.v1` packets and a
  `bot_scorecard_index.v1` ranking.

## Interpretation Rules

- Treat movement as evidence of change, not proof of cause.
- Use at least two supporting dimensions before recommending an action.
- Separate Akamai-provided fields such as `bot_score`, `bot_category`, and
  `bot_type` from Hydrolix-derived fields such as `bot_class`,
  `bot_confidence`, and `bot_intent`. Divergence is investigative evidence, not
  an automatic error.
- Be explicit about `hdx_cdn` when comparing Akamai SIEM, Akamai DS2, and other
  CDN sources because feeds may overlap.
- Remember that `response_status_code` is a string unless explicitly cast.
- Keep raw fallback windows tight and state why fallback was required.
- Do not treat missing feature inputs or unavailable SIEM enrichment as safe
  evidence.
- Treat `cache_origin_impact_report.v1` as a mechanical candidate report. It can
  identify cache-busting or origin-impact candidates, but it must not claim
  causality or recommend mitigations without external evidence.
- Treat `origin_pressure_score` as an investigative proxy, not a billing,
  capacity, or real cost unit.

## Where To Go Next

- Query patterns and SQL templates: `references/soc-analysis.md`,
  `references/seo-analysis.md`, `references/edge-ops-analysis.md`, and
  `references/executive-analysis.md`.
- Structured cache-busting and origin-impact detector contract:
  `references/cache-origin-impact.md`.
- Baseline packet schemas and comparison rules:
  `references/baseline-comparison.md`.
- Policy collateral and displacement review:
  `references/policy-collateral-analysis.md`.
- Scorecard input/output schemas and templates:
  `references/scorecard-analysis.md`.
- Summary table inventory: `references/summary-tables.md`.
- Schema inventory and source coverage: `references/schema.md`.
- Known pitfalls: `references/pitfalls.md`.

## Planned Analyses

The planned analyses below are based on
the user-level advanced-attribution design package in `~/src/plans`. They are
design-direction items, not current deterministic support. Until implemented,
the current supported attribution path remains the simple single-dimension
`bot_mover_attribution.v1` output from `scripts/compare_posture.py`.

| Planned analysis | Intended support | Meaning |
|------------------|------------------|---------|
| Advanced attribution report | New `bot_attribution_report.v1` schema and separate `scripts/attribution.py` normalizer | A stricter attribution packet for current-versus-baseline movement with explicit provenance, limitations, and confidence. |
| Composite dimension attribution | Dimension sets such as `request_host + bot_class`, `request_path_norm + bot_class`, `request_path_norm + asn_type`, `client_asn + ai_category`, and `client_asn + bot_class` | Shows movement by grouped combinations instead of one dimension at a time when a retained summary-table dimension set supports it. |
| Presence lifecycle classification | Labels such as `new`, `disappeared`, `existing`, `absent`, and `not_evaluated` | Distinguishes entities that newly appear or disappear from entities whose metric value simply changed. |
| Support-change classification | Labels such as `support_increase`, `support_decrease`, `support_unchanged`, `support_zero_both`, and `not_evaluated` | Separates request/support movement from the selected metric value. |
| Sparse lifecycle candidates | Separate sparse flags for low-support new or disappeared entities | Preserves useful weak signals without overstating confidence for tiny counts. |
| Complete-scope contribution safeguards | Trusted complete-scope denominator evidence and explicit limitations when contribution is unsafe | Prevents limited rowsets or caller assertions from being interpreted as full-scope attribution. |
| Trusted attribution SQL/template generator | Reviewed `bot-insights-attribution-sql` component that emits SQL/templates and provenance metadata | Provides a controlled path for high-confidence attribution without putting database clients or credentials in local scripts. |
| Scorecard handoff metadata | Future `bot_scorecard_input.v1` handoff after scorecard export hardening | Separates preserve-safe provided contribution from compute-safe complete-rowset contribution before scorecard use. |
| Multiple metrics per report | Later-phase attribution support | Lets one report compare several reviewed metrics instead of one selected metric. |
| Multiple dimension sets per report | Later-phase attribution support | Lets one report include several attribution cuts, such as ASN, path, and class, with separate limitations. |
| Parent-child rollups | Later-phase nested attribution, such as ASN results with nested path movers | Supports drilldown from a top-level mover to the sub-entities driving it. |
| Metric and dimension registries | Reviewed allowlists and alias maps | Documents which metrics are additive, contribution-safe, lifecycle-safe, or display-only. |
| Timeline reconstruction | Later-phase temporal reconstruction | Shows how movement unfolded across buckets instead of only current versus baseline totals. |
| Seasonal or rolling baselines | Later-phase baseline methods beyond provided aggregate rows | Adds rolling or seasonal comparison support while preserving explicit baseline semantics. |
| Advanced displacement attribution | Current `scripts/attribution.py --analysis policy_displacement` support | Extends policy collateral/displacement checks into a fuller attribution report with positive and negative movement summaries when retained-dimension aggregate evidence exists. |
| High-cardinality attribution summaries | Possible summaries such as `bot_agg_asn_path_hour` or `bot_agg_asn_path_day` after cardinality validation | Enables safer ASN-plus-path attribution without raw request-level scans. |
