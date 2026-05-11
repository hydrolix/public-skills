# Bot \& Edge Movement — www\.example\.com, week of 2026\-04\-07 → 2026\-04\-14

How bot traffic and edge health shifted vs the prior week\.

Report type: `executive_posture`

Scope: www\.example\.com · table `bi_summary_day`
Window: 2026\-04\-07 → 2026\-04\-14 vs 2026\-03\-31 → 2026\-04\-07
## Executive Summary

**Total requests up \+20% week\-over\-week — ASN 64500 covers 87\.2% of the increase.**

3 metrics need attention\.

**Recommended action:** Investigate the volume mover\.

## Metric Deltas

| Metric | Current | Baseline | Δ | % change | Verdict | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| Bot share | 34 | 29 | +6 | +18.97% | Investigate | high |
| 429 rate | 2 | 0.90 | +1 | +120.00% | Investigate | high |
| Total requests | 1.50M | 1.25M | +250.00K | +20.00% | Investigate | high |

## Top Mover

**ASN 64500** covers 87.18% of the total requests move.

| ASN | Current | Baseline | Δ | Covers |
| --- | --- | --- | --- | --- |
| 64500 | 420.00K | 80.00K | +340.00K | 87.18% |
| 64600 | 260.00K | 210.00K | +50.00K | 12.82% |

## Recommended next steps

- Break down request volume by ASN, host, and bot class for the affected window\. _(Total requests)_
- Pull rate\-limit policy for known good crawlers and check policy collateral\. _(429 rate)_
- Compare crawler/AI populations vs\. prior week; check policy surfaces\. _(Bot share)_
## Method

Rule-based scorecard for Www\.example\.com, compared against week over week. Reports what was measured, not why. Missing feature inputs are reported as missing — they are not scored as safe.

- Schema: `bot_posture_movement.v1`
- Comparison: Week over week
- Table: `bi_summary_day`
- Constraints: Movement only; No causal claim; LLM may summarize structured evidence only

Generated 2026-05-11 15:52 UTC