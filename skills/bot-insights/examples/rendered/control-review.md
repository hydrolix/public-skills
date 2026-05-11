# Control Review — policy\-bot\-block\-1 · www\.example\.com · window ending 2026\-04\-01 → 2026\-04\-08

Effectiveness review across 1 metric for policy\-bot\-block\-1, comparing the after\-window against the expected baseline\.

Report type: `control_review`

Scope: www\.example\.com · table `bi_siem_policy_summary_day`
## Target

**policy\-bot\-block\-1**

- Before: 2026\-03\-25 00:00 → 2026\-04\-01 00:00 UTC
- After: 2026\-04\-01 00:00 → 2026\-04\-08 00:00 UTC
- Expected basis window: 2026\-03\-25 00:00 → 2026\-04\-01 00:00 UTC
- Expected basis: Explicit target

## Executive Summary

**Overshoot vs expected on SIEM blocked requests for policy\-bot\-block\-1 \(absolute delta \+180\.00 vs expected; \+180\.00% vs expected\).**

Movement compared against the expected baseline\. Per\-metric direction and magnitude appear in the effects table below\. Expected basis: explicit target\. Side\-effect checks: 1 collateral check moved and 1 displacement check moved\.

**Recommended action:** Investigate the magnitude before letting the control ride; consider rolling back or tightening if side effects are material\.

Movement is descriptive, not causal — concurrent changes can confound the read\. Collateral or displacement deltas are unavailable; side\-effect magnitude cannot be quantified from this evidence alone\.

## Target Effects

| Metric | Before | After | Expected | Δ vs expected | % vs expected | Status | Confidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SIEM blocked requests | 90.0 | 280.0 | 100.0 | +180.00 | +180.00% | Increased | high |

## Collateral Checks

| Metric | Before | After | Δ | % change | Status | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| 429 rate | 0.4 | 2.1 | — | — | Increased | — |

## Displacement Checks

| Metric | Before | After | Δ | % change | Status | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| Total requests | 1200000.0 | 1100000.0 | — | — | Decreased | — |

## Method

Rule-based control review for Www\.example\.com, compared against post change vs expected.

- Schema: `bot_control_review.v1`
- Comparison: Post change vs expected
- Table: `bi_siem_policy_summary_day`
- Constraints: Control effectiveness review; No causal claim without external change evidence; LLM may summarize structured evidence only
- Confidence reasons: Baseline window has enough rows; Comparable windows available; Current window has enough rows; Granularity matches comparison; Dimensions fit retained schema; Summary table used

Generated 2026-05-11 15:52 UTC