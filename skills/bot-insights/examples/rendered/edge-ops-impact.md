# Edge \& Origin Cost — Prod, ASN impact queue

Top entities ranked by triggered cache\-busting and origin\-impact signals for the current window\.

Report type: `edge_ops_impact`

Scope: prod / bot\_insights · request host `www\.example\.com` · table `bi_summary_hour`
Entity axis: ASN

Window: 2026\-04\-07T00:00:00Z → 2026\-04\-14T00:00:00Z vs 2026\-03\-31T00:00:00Z → 2026\-04\-07T00:00:00Z
## Executive Summary

**2 of 3 ASNs need analyst attention — start with ASN 64500 \(top 2 entities concentrate 64% of origin pressure; top path /api/v1/pricing carries 72% of cache misses\).**

1 to watch\.

**Recommended action:** Investigate origin cost share \(affects 1 ASN\)\.

## Triage

| State | Asns |
| --- | --- |
| Assign | 2 |
| Watch | 1 |
| Insufficient data | 0 |
| Close — expected | 0 |

2 ASNs need analyst attention; 1 to watch \(out of 3\)\.

## Edge & Origin Evidence

### ASN 64500 — Assign (score 78)

**Edge & origin signals:**

| Rule | Points |
| --- | --- |
| Origin Cost Contribution High | 24 |
| Origin P95 Delta High | 16 |
| Cache Miss Rate High | 12 |
| Query String Diversity With High Miss Rate | 10 |

- **Origin Cost Contribution High:** Origin cost contribution is 42\.00% of fleet origin pressure\.

- **Origin P95 Delta High:** Origin p95 latency increased by 310\.00%\.

- **Cache Miss Rate High:** Cache\-miss rate is 68\.00%\.

- **Query String Diversity With High Miss Rate:** Query\-string diversity is 1240 unique QS with high miss rate\.

### ASN 64600 — Assign (score 50)

**Edge & origin signals:**

| Rule | Points |
| --- | --- |
| Origin Cost Contribution High | 18 |
| Cache Miss Rate High | 12 |
| Cache Miss Delta High | 12 |
| Query String Diversity High | 8 |

- **Origin Cost Contribution High:** Origin cost contribution is 22\.00% of fleet origin pressure\.

- **Cache Miss Rate High:** Cache\-miss rate is 72\.00%\.

- **Cache Miss Delta High:** Cache\-miss rate increased by 14 pp week\-over\-week\.

- **Query String Diversity High:** Query\-string diversity is 890 unique QS\.

### ASN 64700 — Watch (score 14)

**Edge & origin signals:**

| Rule | Points |
| --- | --- |
| Cache Miss Rate High | 12 |

- **Cache Miss Rate High:** Cache\-miss rate is 54\.00%\.

## Queue

| Rank | ASN | Score | Verdict | Primary domain | Confidence |
| --- | --- | --- | --- | --- | --- |
| 1 | ASN 64500 | 78 | Assign | Origin impact | Medium |
| 2 | ASN 64600 | 50 | Assign | Cache busting | Medium |
| 3 | ASN 64700 | 14 | Watch | Cache busting | Medium |

## Top Paths

3 path candidates ranked by combined cache-miss and origin pressure.

| # | Path | Cache-miss share | Origin pressure share | Score | Band | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | /api/v1/pricing | 72.00% | — | 88 | High review | Medium |
| 2 | /api/v1/inventory | 64.00% | — | 62 | Medium review | Medium |
| 3 | /api/v1/search | 52.00% | — | 34 | Observe | Medium |

- **/api/v1/pricing:** cache\_miss\_pct \+12pp, origin\_p95\_ms\_pct \+37pp, requests\_pct \+7pp

- **/api/v1/inventory:** cache\_miss\_pct \+8pp, origin\_p95\_ms\_pct \+30pp, requests\_pct \+5pp

- **/api/v1/search:** cache\_miss\_pct \+3pp, origin\_p95\_ms\_pct \+6pp, requests\_pct \+4pp

## Domain Score Matrix

| ASN | Score | Cache busting | Origin impact |
| --- | --- | --- | --- |
| ASN 64500 | 78 | 22 | 56 |
| ASN 64600 | 50 | 32 | 18 |
| ASN 64700 | 14 | 14 | 0 |

## Recommended Next Steps

- ASN 64500 accounts for 42% of origin requests; confirm whether query\-string variance is driving uncacheable traffic and whether a cache\-key normalization rule would reduce origin load\. _(1 ASN · ASN 64500)_
- Origin p95 jumped 310% this week; check for upstream resource contention or a deployment that changed response characteristics\. _(1 ASN · ASN 64500)_
- 890 unique query strings suggest automated request variance bypassing cache; investigate whether cache\-key normalization or query\-string allow\-listing would reduce miss rate from 72%\. _(1 ASN · ASN 64600)_
- 22% origin cost contribution combined with ASN 64500 totals 64%; verify whether both ASNs represent the same operator and whether coordinated remediation is appropriate\. _(1 ASN · ASN 64600)_
## Coverage

| Domain | Triggered | Evaluated clean | Missing inputs |
| --- | --- | --- | --- |
| Cache busting | 6 | 0 | 1 |
| Origin impact | 3 | 0 | 0 |

## Method

Edge / origin impact review for Prod · Bot Insights, compared against week over week.

- Schema: `bot_scorecard_artifacts.v1`
- Comparison: Week over week
- Table: `bi_summary_hour`
- Constraints: Rule\-based scorecard; Mechanical features only; No causal claim; LLM may summarize structured evidence only
- Confidence reasons: Baseline window has enough rows; Current window has enough rows; Dimensions fit retained schema; Summary table used

Generated 2026-05-11 15:52 UTC