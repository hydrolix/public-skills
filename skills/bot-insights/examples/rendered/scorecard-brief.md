# Demo — www\.example\.com

Assign\. 5 rules triggered across 2 domains\. 2 rules could not be scored\. Score 62\.

Report type: `scorecard_entity_review`

Scope: demo / akamai · table `akamai.bi_summary_hour`
Window: 2026\-05\-02T00:00:00Z → 2026\-05\-03T00:00:00Z vs 2026\-05\-01T00:00:00Z → 2026\-05\-02T00:00:00Z
## Verdict

**Assign** · score 62 · Medium confidence · primary domain Cache busting

_Low confidence: 2 of 7 rules missing inputs_

## Executive Summary

**Score 62 — 5 rules triggered in Cache busting.**

Triggered: Query String Diversity High, Query String Diversity With High Miss Rate, Volume Delta High, and 2 more\. 2 additional rules could not be scored due to missing inputs — treat the score as a floor on risk, not a complete picture\.

**Analyst note (AI assistant):**
The selected host is the top\-ranked scorecard entity because cache\-busting and movement rules crossed thresholds\. Treat the finding as a prioritization cue: validate query\-string behavior and cache policy before attributing cause or changing controls\.

## Triggered Rules

| Rule | Domain | Current | Baseline | Threshold | Points |
| --- | --- | --- | --- | --- | --- |
| Query String Diversity High | Cache busting | 0.82 | — | 0.5 | 16 |
| Query String Diversity With High Miss Rate | Cache busting | 0.82 | — | 0.5 | 18 |
| Volume Delta High | Movement | 300000 | 120000 | 100 | 12 |
| Bot Share Delta High | Movement | 42 | 24 | 10 | 8 |
| Cache Miss Delta High | Cache busting | 64 | 42 | 15 | 8 |

- **Query String Diversity High:** Query\-string diversity ratio is 0\.82\.

- **Query String Diversity With High Miss Rate:** High query\-string diversity coincides with 64% cache misses\.

- **Volume Delta High:** Request volume increased by 180000 \(150%\)\.

- **Bot Share Delta High:** Bot share increased by 18 percentage points\.

- **Cache Miss Delta High:** Cache miss rate increased by 22 percentage points\.

## Recommended Next Steps

- Review mover attribution for the same scope and confirm comparable current/baseline windows\. _(www\.example\.com)_
- Inspect query\-string diversity, cache\-key behavior, and cache miss concentration by host and path\. _(www\.example\.com)_
- Regenerate aggregate rows with SIEM and origin\-latency fields if security or origin\-impact review is required\. _(www\.example\.com)_
## Coverage Detail

2 rules unscored — inputs missing from the source row. These are not scored as safe.

### Origin impact

- `origin\_p95\_delta\_high` — missing: current\_origin\_p95\_ms, baseline\_origin\_p95\_ms
### Security evidence

- `siem\_blocked\_present` — missing: siem\_blocked\_requests
## Method

Rule-based scorecard for Demo · Akamai, compared against previous window. Missing feature inputs are reported as missing — they are not scored as safe.

- Schema: `bot_entity_scorecard.v1`
- Comparison: Previous window
- Table: `akamai.bi_summary_hour`
- Constraints: Rule\-based scorecard; Mechanical features only; No causal claim; LLM may summarize structured evidence only
- Confidence reasons: Baseline window has enough rows; Current window has enough rows; Some feature inputs missing; Dimensions fit retained schema; Summary table used

Generated 2026-05-11 15:52 UTC