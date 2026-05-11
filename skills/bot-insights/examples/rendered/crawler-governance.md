# Crawler Governance — www\.example\.com, AI category health queue

Top crawler entities ranked by triggered governance signals for the current window\.

Report type: `crawler_governance`

Scope:  · request host `www\.example\.com` · table `bi_summary_hour`
Entity axis: AI category

Window: 2026\-04\-07T00:00:00Z → 2026\-04\-14T00:00:00Z vs 2026\-03\-31T00:00:00Z → 2026\-04\-07T00:00:00Z
## Executive Summary

**2 of 2 AI categories need analyst attention — start with AI Training \(80 governance\-surface failures\).**

**Recommended action:** Check good crawler rate limits, 5xx exposure, robots\.

Coverage is thin — 72% of rule evaluations had missing inputs\. Real risk may be higher than the score implies\.

## Triage

| State | AI Categories |
| --- | --- |
| Assign | 2 |
| Watch | 0 |
| Insufficient data | 0 |
| Close — expected | 0 |

2 AI categories need analyst attention \(out of 2\)\.

## Crawler Governance Evidence

### AI Training — Assign (score 64)

**Crawler-governance signals:**

| Rule | Points |
| --- | --- |
| Policy Surface Failure Present | 16 |
| Good Bot 429 Present | 14 |
| Good Bot Error Rate High | 12 |
| AI Crawler Growth High | 10 |

- **Policy Surface Failure Present:** Governance surfaces have 80 failed requests\.

- **Good Bot 429 Present:** Good bot traffic has 120 429 responses\.

- **Good Bot Error Rate High:** Good bot error rate is 6\.50%\.

- **AI Crawler Growth High:** AI crawler metric increased by 344\.44%\.

**Supporting signals:**

| Rule | Domain | Points |
| --- | --- | --- |
| Volume Delta High | Movement | 12 |

### Search Crawler — Assign (score 50)

**Crawler-governance signals:**

| Rule | Points |
| --- | --- |
| Policy Surface Failure Present | 16 |
| Good Bot 429 Present | 14 |
| Good Bot Error Rate High | 12 |
| Rate 429 Delta High | 8 |

- **Policy Surface Failure Present:** Governance surfaces have 120 failed requests\.

- **Good Bot 429 Present:** Good bot traffic has 7400 429 responses\.

- **Good Bot Error Rate High:** Good bot error rate is 8\.20%\.

- **Rate 429 Delta High:** 429 rate increased by 6\.3 percentage points\.

## Queue

| Rank | AI Category | Score | Verdict | Primary domain | Confidence |
| --- | --- | --- | --- | --- | --- |
| 1 | AI Training | 64 | Assign | Crawler governance | Medium |
| 2 | Search Crawler | 50 | Assign | Crawler governance | Medium |

## Domain Score Matrix

| AI Category | Score | Crawler governance | Movement |
| --- | --- | --- | --- |
| AI Training | 64 | 52 | 12 |
| Search Crawler | 50 | 50 | 0 |

## Recommended Next Steps

- Check good crawler rate limits, 5xx exposure, robots\.txt, llms\.txt, and sitemap availability\. _(2 AI categories · AI Training, Search Crawler)_
- Review mover attribution for the same scope and confirm comparable current/baseline windows\. _(1 AI category · AI Training)_
## Coverage

| Domain | Triggered | Evaluated clean | Missing inputs |
| --- | --- | --- | --- |
| Crawler governance | 8 | 0 | 1 |
| Cache busting | 0 | 0 | 8 |
| Movement | 1 | 0 | 4 |
| Origin impact | 0 | 0 | 4 |
| Security evidence | 0 | 0 | 6 |

## Method

Crawler governance review for , compared against week over week.

- Schema: `bot_scorecard_artifacts.v1`
- Comparison: Week over week
- Table: `bi_summary_hour`
- Constraints: Rule\-based scorecard; Mechanical features only; No causal claim; LLM may summarize structured evidence only
- Confidence reasons: Baseline window has enough rows; Current window has enough rows; Some feature inputs missing; Dimensions fit retained schema; SIEM data unavailable; Summary table used

Generated 2026-05-11 15:52 UTC