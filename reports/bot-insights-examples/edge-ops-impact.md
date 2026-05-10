# Edge &amp; Origin Cost \- www\.example\.com \- 2026\-04\-14

Report type: `edge_ops_impact`

Scope: www\.example\.com

## Analyst Notes

These notes are interpretive narrative, not facts strictly proven by artifact data alone.

### Note 1

_LLM interpretation._ Two ASNs concentrate 64% of origin pressure, driven by high cache\-miss rates and query\-string diversity patterns; start triage with ASN 64500\.

Supporting evidence:

- top entity score: 78
- top\-ranked entity: 64\.50K

## Edge/Ops Impact Summary

Rows follow scorecard index order.

| Entity type | Entity | Score | Relevant features | Confidence |
| --- | --- | --- | --- | --- |
| client\_asn | 64500 | 78 | origin\_cost\_contribution\_high, origin\_p95\_delta\_high, cache\_miss\_rate\_high, querystring\_diversity\_with\_high\_miss\_rate | medium |
| client\_asn | 64600 | 50 | origin\_cost\_contribution\_high, cache\_miss\_rate\_high, querystring\_diversity\_high, cache\_miss\_delta\_high | medium |
| client\_asn | 64700 | 14 | cache\_miss\_rate\_high | medium |

## Edge/Ops Impact Evidence

### 64500

| Domain | Feature | Condition | Points | Evidence |
| --- | --- | --- | --- | --- |
| Origin Impact | Origin Cost Contribution | High | 24 | Origin cost contribution is 42% of fleet origin pressure\. |
| Origin Impact | Origin P95 | High Increase | 16 | Origin p95 latency increased by 310%\. |
| Cache Busting | Cache Miss Rate | High | 12 | Cache\-miss rate is 68%\. |
| Cache Busting | Query String Diversity | With High Miss Rate | 10 | Query\-string diversity is 1240 unique QS with high miss rate\. |

### 64600

| Domain | Feature | Condition | Points | Evidence |
| --- | --- | --- | --- | --- |
| Origin Impact | Origin Cost Contribution | High | 18 | Origin cost contribution is 22% of fleet origin pressure\. |
| Cache Busting | Cache Miss Rate | High | 12 | Cache\-miss rate is 72%\. |
| Cache Busting | Query String Diversity | High | 8 | Query\-string diversity is 890 unique QS\. |
| Cache Busting | Cache Miss Rate | High Increase | 12 | Cache\-miss rate increased by 14 pp week\-over\-week\. |

### 64700

| Domain | Feature | Condition | Points | Evidence |
| --- | --- | --- | --- | --- |
| Cache Busting | Cache Miss Rate | High | 12 | Cache\-miss rate is 54%\. |

## Evidence Limits

### Artifact edge\-pack\-1

- Schema: bot\_scorecard\_artifacts\.v1
- Table: unavailable
- Scope: unavailable
- Confidence: unavailable
- Confidence reasons: unavailable
- Interpretation constraints: unavailable
- Producer limits: result\_row\_count=3, producer\_limit=5, result\_truncated=false, total\_ranked\_entities=3

### Artifact edge\-pack\-1\#index

- Schema: bot\_scorecard\_index\.v1
- Parent: edge\-pack\-1 at /index
- Table: bi\_summary\_hour
- Scope: cluster=prod, database=bot\_insights, request\_host=www\.example\.com
- Confidence: unavailable
- Confidence reasons: unavailable
- Interpretation constraints: rule\_based\_scorecard, mechanical\_features\_only, no\_causal\_claim, llm\_may\_summarize\_structured\_evidence\_only
- Windows: current 2026\-04\-07 00:00 UTC to 2026\-04\-14 00:00 UTC; baseline 2026\-03\-31 00:00 UTC to 2026\-04\-07 00:00 UTC
- Producer limits: result\_row\_count=3, producer\_limit=5, result\_truncated=false, total\_ranked\_entities=3

### Artifact edge\-pack\-1\#scorecard\-1

- Schema: bot\_entity\_scorecard\.v1
- Parent: edge\-pack\-1 at /scorecards/0
- Table: bi\_summary\_hour
- Scope: cluster=prod, database=bot\_insights, request\_host=www\.example\.com
- Confidence: medium
- Confidence reasons: summary\_table\_used, retained\_dimensions\_fit, current\_count\_sufficient, baseline\_count\_sufficient
- Interpretation constraints: rule\_based\_scorecard, mechanical\_features\_only, no\_causal\_claim, llm\_may\_summarize\_structured\_evidence\_only
- Windows: current 2026\-04\-07 00:00 UTC to 2026\-04\-14 00:00 UTC; baseline 2026\-03\-31 00:00 UTC to 2026\-04\-07 00:00 UTC
- Not-evaluated features:
  - cache\_busting / cache\_miss\_delta\_high (missing inputs: baseline\_cache\_miss\_pct; reason: feature\_input\_missing)
- Domain score ambiguity: emitted numeric domain scores are rendered as-is; missing inputs remain unresolved for cache\_busting.

### Artifact edge\-pack\-1\#scorecard\-2

- Schema: bot\_entity\_scorecard\.v1
- Parent: edge\-pack\-1 at /scorecards/1
- Table: bi\_summary\_hour
- Scope: cluster=prod, database=bot\_insights, request\_host=www\.example\.com
- Confidence: medium
- Confidence reasons: summary\_table\_used, retained\_dimensions\_fit, current\_count\_sufficient, baseline\_count\_sufficient
- Interpretation constraints: rule\_based\_scorecard, mechanical\_features\_only, no\_causal\_claim, llm\_may\_summarize\_structured\_evidence\_only
- Windows: current 2026\-04\-07 00:00 UTC to 2026\-04\-14 00:00 UTC; baseline 2026\-03\-31 00:00 UTC to 2026\-04\-07 00:00 UTC

### Artifact edge\-pack\-1\#scorecard\-3

- Schema: bot\_entity\_scorecard\.v1
- Parent: edge\-pack\-1 at /scorecards/2
- Table: bi\_summary\_hour
- Scope: cluster=prod, database=bot\_insights, request\_host=www\.example\.com
- Confidence: medium
- Confidence reasons: summary\_table\_used, retained\_dimensions\_fit, current\_count\_sufficient, baseline\_count\_sufficient
- Interpretation constraints: rule\_based\_scorecard, mechanical\_features\_only, no\_causal\_claim, llm\_may\_summarize\_structured\_evidence\_only
- Windows: current 2026\-04\-07 00:00 UTC to 2026\-04\-14 00:00 UTC; baseline 2026\-03\-31 00:00 UTC to 2026\-04\-07 00:00 UTC

### Artifact artifact\-2

- Schema: cache\_origin\_impact\_report\.v1
- Table: unavailable
- Scope: request\_host=www\.example\.com
- Confidence: medium
- Confidence reasons: summary\_table\_used, current\_count\_sufficient, baseline\_count\_sufficient
- Interpretation constraints: mechanical\_candidate\_only, no\_causal\_claim, origin\_pressure\_score\_is\_proxy, not\_a\_billing\_or\_capacity\_unit, llm\_may\_summarize\_structured\_evidence\_only
- Windows: current 2026\-04\-07 00:00 UTC to 2026\-04\-14 00:00 UTC; baseline 2026\-03\-31 00:00 UTC to 2026\-04\-07 00:00 UTC

Reports use emitted artifact fields only. Missing evidence is unavailable, not zero or safe.

## Warnings

- Edge/Ops Impact report found 1 relevant missing feature inputs\.
