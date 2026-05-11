# SOC Triage — www\.example\.com, ASN risk queue

Top entities ranked by mechanical risk indicators for the current window\.

Report type: `soc_triage`

Scope:  · request host `www\.example\.com` · table `bi_summary_hour`
Entity axis: ASN

Window: 2026\-04\-07T00:00:00Z → 2026\-04\-14T00:00:00Z vs 2026\-03\-31T00:00:00Z → 2026\-04\-07T00:00:00Z
## Executive Summary

**1 of 2 ASN needs analyst attention — start with ASN 64500 \(bad\-bot share 65%, SIEM evidence present\).**

SOC investigate ASN 64500 now; monitor / enrich ASN 64600\.

**Recommended action:** Enrich with SIEM action, policy, auth\-failure, and blocked\-request summaries for the same entity\.

Coverage is thin — 80% of rule evaluations had missing inputs\. Real risk may be higher than the score implies\.

## Triage

| State | Asns |
| --- | --- |
| Assign | 1 |
| Watch | 1 |
| Insufficient data | 0 |
| Close — expected | 0 |

1 ASN needs analyst attention; 1 to watch \(out of 2\)\.

## Security Evidence

### ASN 64500 — Assign (score 58)

**Security rules triggered:**

| Rule | Domain | Current | Baseline | Threshold | Points |
| --- | --- | --- | --- | --- | --- |
| Bad Bot Share High | Security evidence | 65 | — | 50 | 14 |
| SIEM Auth Fail Present | Security evidence | 200 | — | 0 | 12 |
| SIEM Blocked Present | Security evidence | 1200 | — | 0 | 12 |

**Other triggered rules:**

| Rule | Domain | Current | Baseline | Threshold | Points |
| --- | --- | --- | --- | --- | --- |
| Volume Delta High | Movement | 420000 | 80000 | 100 | 12 |
| Bot Share Delta High | Movement | 82.1 | 38 | 10 | 8 |

### ASN 64600 — Watch (score 12)

**Security rules triggered:**

| Rule | Domain | Current | Baseline | Threshold | Points |
| --- | --- | --- | --- | --- | --- |
| SIEM Blocked Present | Security evidence | 40 | — | 0 | 12 |

## Queue

| Rank | ASN | Score | Verdict | Primary domain | Confidence |
| --- | --- | --- | --- | --- | --- |
| 1 | ASN 64500 | 58 | Assign | Security evidence | Medium |
| 2 | ASN 64600 | 12 | Watch | Security evidence | Medium |

## Domain Score Matrix

| ASN | Score | Cache busting | Crawler governance | Movement | Origin impact | Policy collateral | Security evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ASN 64500 | 58 | 0 | 0 | 20 | 0 | 0 | 38 |
| ASN 64600 | 12 | 0 | 0 | 0 | 0 | 0 | 12 |

## Recommended Next Steps

- Enrich with SIEM action, policy, auth\-failure, and blocked\-request summaries for the same entity\. _(2 ASNs · ASN 64500, ASN 64600)_
- Review mover attribution for the same scope and confirm comparable current/baseline windows\. _(1 ASN · ASN 64500)_
## Coverage

| Domain | Triggered | Evaluated clean | Missing inputs |
| --- | --- | --- | --- |
| Cache busting | 0 | 0 | 8 |
| Crawler governance | 0 | 0 | 9 |
| Movement | 2 | 0 | 2 |
| Origin impact | 0 | 0 | 4 |
| Security evidence | 4 | 0 | 1 |

## Method

SOC triage for , compared against week over week.

- Schema: `bot_scorecard_artifacts.v1`
- Comparison: Week over week
- Table: `bi_summary_hour`
- Constraints: Rule\-based scorecard; Mechanical features only; No causal claim; LLM may summarize structured evidence only
- Confidence reasons: Baseline window has enough rows; Current window has enough rows; Some feature inputs missing; Dimensions fit retained schema; Summary table used

Generated 2026-05-11 15:52 UTC