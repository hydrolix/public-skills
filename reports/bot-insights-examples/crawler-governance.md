# Crawler Governance \- www\.example\.com

Report type: `crawler_governance`

Scope: www\.example\.com

## Crawler Governance Summary

Rows follow scorecard index order.

| Entity type | Entity | Score | Relevant features | Confidence |
| --- | --- | --- | --- | --- |
| ai\_category | ai\_training | 64 | ai\_crawler\_growth\_high, good\_bot\_429\_present, good\_bot\_error\_rate\_high, policy\_surface\_failure\_present | medium |
| ai\_category | search\_crawler | 50 | good\_bot\_429\_present, good\_bot\_error\_rate\_high, policy\_surface\_failure\_present, rate\_429\_delta\_high | medium |

## Crawler Governance Evidence

### ai\_training

| Domain | Feature | Points | Evidence |
| --- | --- | --- | --- |
| crawler\_governance | ai\_crawler\_growth\_high | 10 | AI crawler metric increased by 344\.444444%\. |
| crawler\_governance | good\_bot\_429\_present | 14 | Good bot traffic has 120 429 responses\. |
| crawler\_governance | good\_bot\_error\_rate\_high | 12 | Good bot error rate is 6\.5%\. |
| crawler\_governance | policy\_surface\_failure\_present | 16 | Governance surfaces have 80 failed requests\. |

### search\_crawler

| Domain | Feature | Points | Evidence |
| --- | --- | --- | --- |
| crawler\_governance | good\_bot\_429\_present | 14 | Good bot traffic has 7400 429 responses\. |
| crawler\_governance | good\_bot\_error\_rate\_high | 12 | Good bot error rate is 8\.2%\. |
| crawler\_governance | policy\_surface\_failure\_present | 16 | Governance surfaces have 120 failed requests\. |
| crawler\_governance | rate\_429\_delta\_high | 8 | 429 rate increased by 6\.3 percentage points\. |

## Evidence Limits

### Artifact crawler\-pack\-1

- Schema: bot\_scorecard\_artifacts\.v1
- Table: unavailable
- Scope: unavailable
- Confidence: unavailable
- Confidence reasons: unavailable
- Interpretation constraints: unavailable
- Producer limits: result\_row\_count=2, producer\_limit=5, result\_truncated=false, total\_ranked\_entities=2

### Artifact crawler\-pack\-1\#index

- Schema: bot\_scorecard\_index\.v1
- Parent: crawler\-pack\-1 at /index
- Table: bi\_summary\_hour
- Scope: request\_host=www\.example\.com
- Confidence: unavailable
- Confidence reasons: unavailable
- Interpretation constraints: rule\_based\_scorecard, mechanical\_features\_only, no\_causal\_claim, llm\_may\_summarize\_structured\_evidence\_only
- Windows: current\_window: \{"end": "2026\-04\-14T00:00:00Z","start": "2026\-04\-07T00:00:00Z"\}; baseline\_windows: \[\{"end": "2026\-04\-07T00:00:00Z","start": "2026\-03\-31T00:00:00Z"\}\]
- Producer limits: result\_row\_count=2, producer\_limit=5, result\_truncated=false, total\_ranked\_entities=2

### Artifact crawler\-pack\-1\#scorecard\-1

- Schema: bot\_entity\_scorecard\.v1
- Parent: crawler\-pack\-1 at /scorecards/0
- Table: bi\_summary\_hour
- Scope: request\_host=www\.example\.com
- Confidence: medium
- Confidence reasons: summary\_table\_used, retained\_dimensions\_fit, current\_count\_sufficient, baseline\_count\_sufficient, siem\_unavailable, feature\_input\_missing
- Interpretation constraints: rule\_based\_scorecard, mechanical\_features\_only, no\_causal\_claim, llm\_may\_summarize\_structured\_evidence\_only
- Windows: current\_window: \{"end": "2026\-04\-14T00:00:00Z","start": "2026\-04\-07T00:00:00Z"\}; baseline\_windows: \[\{"end": "2026\-04\-07T00:00:00Z","start": "2026\-03\-31T00:00:00Z"\}\]
- Not-evaluated features:
  - cache\_busting / cache\_miss\_delta\_high (missing inputs: baseline\_cache\_miss\_pct, current\_cache\_miss\_pct; reason: feature\_input\_missing)
  - cache\_busting / cache\_miss\_rate\_high (missing inputs: cache\_miss\_pct; reason: feature\_input\_missing)
  - cache\_busting / querystring\_diversity\_high (missing inputs: qs\_diversity\_ratio; reason: feature\_input\_missing)
  - cache\_busting / querystring\_diversity\_with\_high\_miss\_rate (missing inputs: cache\_miss\_pct, qs\_diversity\_ratio; reason: feature\_input\_missing)
  - crawler\_governance / rate\_5xx\_delta\_high (missing inputs: baseline\_rate\_5xx\_pct, current\_rate\_5xx\_pct; reason: feature\_input\_missing)
  - movement / bot\_share\_delta\_high (missing inputs: baseline\_bot\_share\_pct, current\_bot\_share\_pct; reason: feature\_input\_missing)
  - movement / contribution\_to\_total\_delta\_high (missing inputs: contribution\_pct; reason: feature\_input\_missing)
  - origin\_impact / origin\_cost\_contribution\_high (missing inputs: origin\_cost\_contribution\_pct; reason: feature\_input\_missing)
  - origin\_impact / origin\_p95\_delta\_high (missing inputs: baseline\_origin\_p95\_ms, current\_origin\_p95\_ms; reason: feature\_input\_missing)
  - security\_evidence / bad\_bot\_share\_high (missing inputs: bad\_bot\_share\_pct; reason: feature\_input\_missing)
  - security\_evidence / siem\_auth\_fail\_present (missing inputs: siem\_auth\_fail\_requests; reason: feature\_input\_missing)
  - security\_evidence / siem\_blocked\_present (missing inputs: siem\_blocked\_requests; reason: feature\_input\_missing)
- Domain score ambiguity: emitted numeric domain scores are rendered as-is; missing inputs remain unresolved for cache\_busting, crawler\_governance, movement, origin\_impact, security\_evidence.

### Artifact crawler\-pack\-1\#scorecard\-2

- Schema: bot\_entity\_scorecard\.v1
- Parent: crawler\-pack\-1 at /scorecards/1
- Table: bi\_summary\_hour
- Scope: request\_host=www\.example\.com
- Confidence: medium
- Confidence reasons: summary\_table\_used, retained\_dimensions\_fit, current\_count\_sufficient, baseline\_count\_sufficient, siem\_unavailable, feature\_input\_missing
- Interpretation constraints: rule\_based\_scorecard, mechanical\_features\_only, no\_causal\_claim, llm\_may\_summarize\_structured\_evidence\_only
- Windows: current\_window: \{"end": "2026\-04\-14T00:00:00Z","start": "2026\-04\-07T00:00:00Z"\}; baseline\_windows: \[\{"end": "2026\-04\-07T00:00:00Z","start": "2026\-03\-31T00:00:00Z"\}\]
- Not-evaluated features:
  - cache\_busting / cache\_miss\_delta\_high (missing inputs: baseline\_cache\_miss\_pct, current\_cache\_miss\_pct; reason: feature\_input\_missing)
  - cache\_busting / cache\_miss\_rate\_high (missing inputs: cache\_miss\_pct; reason: feature\_input\_missing)
  - cache\_busting / querystring\_diversity\_high (missing inputs: qs\_diversity\_ratio; reason: feature\_input\_missing)
  - cache\_busting / querystring\_diversity\_with\_high\_miss\_rate (missing inputs: cache\_miss\_pct, qs\_diversity\_ratio; reason: feature\_input\_missing)
  - movement / bot\_share\_delta\_high (missing inputs: baseline\_bot\_share\_pct, current\_bot\_share\_pct; reason: feature\_input\_missing)
  - movement / contribution\_to\_total\_delta\_high (missing inputs: contribution\_pct; reason: feature\_input\_missing)
  - origin\_impact / origin\_cost\_contribution\_high (missing inputs: origin\_cost\_contribution\_pct; reason: feature\_input\_missing)
  - origin\_impact / origin\_p95\_delta\_high (missing inputs: baseline\_origin\_p95\_ms, current\_origin\_p95\_ms; reason: feature\_input\_missing)
  - security\_evidence / bad\_bot\_share\_high (missing inputs: bad\_bot\_share\_pct; reason: feature\_input\_missing)
  - security\_evidence / siem\_auth\_fail\_present (missing inputs: siem\_auth\_fail\_requests; reason: feature\_input\_missing)
  - security\_evidence / siem\_blocked\_present (missing inputs: siem\_blocked\_requests; reason: feature\_input\_missing)
- Domain score ambiguity: emitted numeric domain scores are rendered as-is; missing inputs remain unresolved for cache\_busting, movement, origin\_impact, security\_evidence.

Reports use emitted artifact fields only. Missing evidence is unavailable, not zero or safe.

## Warnings

- Crawler Governance report found 1 relevant missing feature inputs\.
