# SOC Triage \- www\.example\.com \- 2026\-04\-14

Report type: `soc_triage`

Scope: www\.example\.com

## Top Risky Entities

| Rank | Entity type | Entity | Score | Band | Primary domain | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | client\_asn | 64500 | 58 | medium\_review | security\_evidence | medium |
| 2 | client\_asn | 64600 | 12 | observe | security\_evidence | medium |

## Scorecard Analysis

### 64500

| Score | Band | Primary domain | Confidence |
| --- | --- | --- | --- |
| 58 | medium\_review | security\_evidence | medium |

**Evidence Summary**

- Bad bot share is 65%\.
- SIEM summary reports 200 auth failures\.
- SIEM summary reports 1200 blocked requests\.
- Request volume increased by 340000 \(425%\)\.
- Bot share increased by 44\.1 percentage points\.
- 11 feature inputs were missing and were not scored as safe\.

**Evaluated Features**

| Domain | Feature | Points | Evidence |
| --- | --- | --- | --- |
| movement | bot\_share\_delta\_high | 8 | Bot share increased by 44\.1 percentage points\. |
| movement | volume\_delta\_high | 12 | Request volume increased by 340000 \(425%\)\. |
| security\_evidence | bad\_bot\_share\_high | 14 | Bad bot share is 65%\. |
| security\_evidence | siem\_auth\_fail\_present | 12 | SIEM summary reports 200 auth failures\. |
| security\_evidence | siem\_blocked\_present | 12 | SIEM summary reports 1200 blocked requests\. |

**Recommended Next Steps**

- Review mover attribution for the same scope and confirm comparable current/baseline windows\.
- Enrich with SIEM action, policy, auth\-failure, and blocked\-request summaries for the same entity\.

### 64600

| Score | Band | Primary domain | Confidence |
| --- | --- | --- | --- |
| 12 | observe | security\_evidence | medium |

**Evidence Summary**

- SIEM summary reports 40 blocked requests\.
- 13 feature inputs were missing and were not scored as safe\.

**Evaluated Features**

| Domain | Feature | Points | Evidence |
| --- | --- | --- | --- |
| security\_evidence | siem\_blocked\_present | 12 | SIEM summary reports 40 blocked requests\. |

**Recommended Next Steps**

- Enrich with SIEM action, policy, auth\-failure, and blocked\-request summaries for the same entity\.

## Domain Score Matrix

| Entity | Total score | cache\_busting | crawler\_governance | movement | origin\_impact | policy\_collateral | security\_evidence | signal\_alignment |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 64500 | 58 | 0 | 0 | 20 | 0 | 0 | 38 | 0 |
| 64600 | 12 | 0 | 0 | 0 | 0 | 0 | 12 | 0 |

## Security Evidence Notes

### 64500

| Domain | Feature | Points | Evidence |
| --- | --- | --- | --- |
| security\_evidence | bad\_bot\_share\_high | 14 | Bad bot share is 65%\. |
| security\_evidence | siem\_auth\_fail\_present | 12 | SIEM summary reports 200 auth failures\. |
| security\_evidence | siem\_blocked\_present | 12 | SIEM summary reports 1200 blocked requests\. |

### 64600

| Domain | Feature | Points | Evidence |
| --- | --- | --- | --- |
| security\_evidence | siem\_blocked\_present | 12 | SIEM summary reports 40 blocked requests\. |

## Missing Feature Evidence

### 64500

| Domain | Feature | Missing inputs | Reason |
| --- | --- | --- | --- |
| cache\_busting | cache\_miss\_delta\_high | baseline\_cache\_miss\_pct, current\_cache\_miss\_pct | feature\_input\_missing |
| cache\_busting | cache\_miss\_rate\_high | cache\_miss\_pct | feature\_input\_missing |
| cache\_busting | querystring\_diversity\_high | qs\_diversity\_ratio | feature\_input\_missing |
| cache\_busting | querystring\_diversity\_with\_high\_miss\_rate | cache\_miss\_pct, qs\_diversity\_ratio | feature\_input\_missing |
| crawler\_governance | ai\_crawler\_growth\_high | baseline\_ai\_crawler\_requests, current\_ai\_crawler\_requests | feature\_input\_missing |
| crawler\_governance | good\_bot\_429\_present | good\_bot\_429\_requests | feature\_input\_missing |
| crawler\_governance | good\_bot\_error\_rate\_high | good\_bot\_error\_rate\_pct | feature\_input\_missing |
| crawler\_governance | policy\_surface\_failure\_present | policy\_surface\_failures | feature\_input\_missing |
| movement | contribution\_to\_total\_delta\_high | contribution\_pct | feature\_input\_missing |
| origin\_impact | origin\_cost\_contribution\_high | origin\_cost\_contribution\_pct | feature\_input\_missing |
| origin\_impact | origin\_p95\_delta\_high | baseline\_origin\_p95\_ms, current\_origin\_p95\_ms | feature\_input\_missing |

### 64600

| Domain | Feature | Missing inputs | Reason |
| --- | --- | --- | --- |
| cache\_busting | cache\_miss\_delta\_high | baseline\_cache\_miss\_pct, current\_cache\_miss\_pct | feature\_input\_missing |
| cache\_busting | cache\_miss\_rate\_high | cache\_miss\_pct | feature\_input\_missing |
| cache\_busting | querystring\_diversity\_high | qs\_diversity\_ratio | feature\_input\_missing |
| cache\_busting | querystring\_diversity\_with\_high\_miss\_rate | cache\_miss\_pct, qs\_diversity\_ratio | feature\_input\_missing |
| crawler\_governance | ai\_crawler\_growth\_high | baseline\_ai\_crawler\_requests, current\_ai\_crawler\_requests | feature\_input\_missing |
| crawler\_governance | good\_bot\_429\_present | good\_bot\_429\_requests | feature\_input\_missing |
| crawler\_governance | good\_bot\_error\_rate\_high | good\_bot\_error\_rate\_pct | feature\_input\_missing |
| crawler\_governance | policy\_surface\_failure\_present | policy\_surface\_failures | feature\_input\_missing |
| crawler\_governance | rate\_5xx\_delta\_high | baseline\_rate\_5xx\_pct, current\_rate\_5xx\_pct | feature\_input\_missing |
| movement | contribution\_to\_total\_delta\_high | contribution\_pct | feature\_input\_missing |
| origin\_impact | origin\_cost\_contribution\_high | origin\_cost\_contribution\_pct | feature\_input\_missing |
| origin\_impact | origin\_p95\_delta\_high | baseline\_origin\_p95\_ms, current\_origin\_p95\_ms | feature\_input\_missing |
| security\_evidence | siem\_auth\_fail\_present | siem\_auth\_fail\_requests | feature\_input\_missing |

## Confidence Notes

| Entity type | Entity | Confidence | Confidence reasons |
| --- | --- | --- | --- |
| client\_asn | 64500 | medium | summary\_table\_used, retained\_dimensions\_fit, current\_count\_sufficient, baseline\_count\_sufficient, feature\_input\_missing |
| client\_asn | 64600 | medium | summary\_table\_used, retained\_dimensions\_fit, current\_count\_sufficient, baseline\_count\_sufficient, feature\_input\_missing |

## Analyst Notes

These notes are interpretive narrative, not facts strictly proven by artifact data alone.

### Note 1

_LLM interpretation._ ASN 64500 shows bad\-bot share above 50 percent with SIEM\-blocked evidence; start triage here\.

- top entity score: scorecard\-pack\-1\#scorecard\-1 /score = 58
- top\-ranked entity: scorecard\-pack\-1\#index /ranked\_entities/0/entity = 64500

## Evidence Limits

### Artifact scorecard\-pack\-1

- Schema: bot\_scorecard\_artifacts\.v1
- Table: unavailable
- Scope: unavailable
- Confidence: unavailable
- Confidence reasons: unavailable
- Interpretation constraints: unavailable
- Producer limits: result\_row\_count=2, producer\_limit=5, result\_truncated=false, total\_ranked\_entities=2

### Artifact scorecard\-pack\-1\#index

- Schema: bot\_scorecard\_index\.v1
- Parent: scorecard\-pack\-1 at /index
- Table: bi\_summary\_hour
- Scope: request\_host=www\.example\.com
- Confidence: unavailable
- Confidence reasons: unavailable
- Interpretation constraints: rule\_based\_scorecard, mechanical\_features\_only, no\_causal\_claim, llm\_may\_summarize\_structured\_evidence\_only
- Windows: current\_window: \{"end": "2026\-04\-14T00:00:00Z","start": "2026\-04\-07T00:00:00Z"\}; baseline\_windows: \[\{"end": "2026\-04\-07T00:00:00Z","start": "2026\-03\-31T00:00:00Z"\}\]
- Producer limits: result\_row\_count=2, producer\_limit=5, result\_truncated=false, total\_ranked\_entities=2

### Artifact scorecard\-pack\-1\#scorecard\-1

- Schema: bot\_entity\_scorecard\.v1
- Parent: scorecard\-pack\-1 at /scorecards/0
- Table: bi\_summary\_hour
- Scope: request\_host=www\.example\.com
- Confidence: medium
- Confidence reasons: summary\_table\_used, retained\_dimensions\_fit, current\_count\_sufficient, baseline\_count\_sufficient, feature\_input\_missing
- Interpretation constraints: rule\_based\_scorecard, mechanical\_features\_only, no\_causal\_claim, llm\_may\_summarize\_structured\_evidence\_only
- Windows: current\_window: \{"end": "2026\-04\-14T00:00:00Z","start": "2026\-04\-07T00:00:00Z"\}; baseline\_windows: \[\{"end": "2026\-04\-07T00:00:00Z","start": "2026\-03\-31T00:00:00Z"\}\]
- Not-evaluated features:
  - cache\_busting / cache\_miss\_delta\_high (missing inputs: baseline\_cache\_miss\_pct, current\_cache\_miss\_pct; reason: feature\_input\_missing)
  - cache\_busting / cache\_miss\_rate\_high (missing inputs: cache\_miss\_pct; reason: feature\_input\_missing)
  - cache\_busting / querystring\_diversity\_high (missing inputs: qs\_diversity\_ratio; reason: feature\_input\_missing)
  - cache\_busting / querystring\_diversity\_with\_high\_miss\_rate (missing inputs: cache\_miss\_pct, qs\_diversity\_ratio; reason: feature\_input\_missing)
  - crawler\_governance / ai\_crawler\_growth\_high (missing inputs: baseline\_ai\_crawler\_requests, current\_ai\_crawler\_requests; reason: feature\_input\_missing)
  - crawler\_governance / good\_bot\_429\_present (missing inputs: good\_bot\_429\_requests; reason: feature\_input\_missing)
  - crawler\_governance / good\_bot\_error\_rate\_high (missing inputs: good\_bot\_error\_rate\_pct; reason: feature\_input\_missing)
  - crawler\_governance / policy\_surface\_failure\_present (missing inputs: policy\_surface\_failures; reason: feature\_input\_missing)
  - movement / contribution\_to\_total\_delta\_high (missing inputs: contribution\_pct; reason: feature\_input\_missing)
  - origin\_impact / origin\_cost\_contribution\_high (missing inputs: origin\_cost\_contribution\_pct; reason: feature\_input\_missing)
  - origin\_impact / origin\_p95\_delta\_high (missing inputs: baseline\_origin\_p95\_ms, current\_origin\_p95\_ms; reason: feature\_input\_missing)
- Domain score ambiguity: emitted numeric domain scores are rendered as-is; missing inputs remain unresolved for cache\_busting, crawler\_governance, movement, origin\_impact.

### Artifact scorecard\-pack\-1\#scorecard\-2

- Schema: bot\_entity\_scorecard\.v1
- Parent: scorecard\-pack\-1 at /scorecards/1
- Table: bi\_summary\_hour
- Scope: request\_host=www\.example\.com
- Confidence: medium
- Confidence reasons: summary\_table\_used, retained\_dimensions\_fit, current\_count\_sufficient, baseline\_count\_sufficient, feature\_input\_missing
- Interpretation constraints: rule\_based\_scorecard, mechanical\_features\_only, no\_causal\_claim, llm\_may\_summarize\_structured\_evidence\_only
- Windows: current\_window: \{"end": "2026\-04\-14T00:00:00Z","start": "2026\-04\-07T00:00:00Z"\}; baseline\_windows: \[\{"end": "2026\-04\-07T00:00:00Z","start": "2026\-03\-31T00:00:00Z"\}\]
- Not-evaluated features:
  - cache\_busting / cache\_miss\_delta\_high (missing inputs: baseline\_cache\_miss\_pct, current\_cache\_miss\_pct; reason: feature\_input\_missing)
  - cache\_busting / cache\_miss\_rate\_high (missing inputs: cache\_miss\_pct; reason: feature\_input\_missing)
  - cache\_busting / querystring\_diversity\_high (missing inputs: qs\_diversity\_ratio; reason: feature\_input\_missing)
  - cache\_busting / querystring\_diversity\_with\_high\_miss\_rate (missing inputs: cache\_miss\_pct, qs\_diversity\_ratio; reason: feature\_input\_missing)
  - crawler\_governance / ai\_crawler\_growth\_high (missing inputs: baseline\_ai\_crawler\_requests, current\_ai\_crawler\_requests; reason: feature\_input\_missing)
  - crawler\_governance / good\_bot\_429\_present (missing inputs: good\_bot\_429\_requests; reason: feature\_input\_missing)
  - crawler\_governance / good\_bot\_error\_rate\_high (missing inputs: good\_bot\_error\_rate\_pct; reason: feature\_input\_missing)
  - crawler\_governance / policy\_surface\_failure\_present (missing inputs: policy\_surface\_failures; reason: feature\_input\_missing)
  - crawler\_governance / rate\_5xx\_delta\_high (missing inputs: baseline\_rate\_5xx\_pct, current\_rate\_5xx\_pct; reason: feature\_input\_missing)
  - movement / contribution\_to\_total\_delta\_high (missing inputs: contribution\_pct; reason: feature\_input\_missing)
  - origin\_impact / origin\_cost\_contribution\_high (missing inputs: origin\_cost\_contribution\_pct; reason: feature\_input\_missing)
  - origin\_impact / origin\_p95\_delta\_high (missing inputs: baseline\_origin\_p95\_ms, current\_origin\_p95\_ms; reason: feature\_input\_missing)
  - security\_evidence / siem\_auth\_fail\_present (missing inputs: siem\_auth\_fail\_requests; reason: feature\_input\_missing)
- Domain score ambiguity: emitted numeric domain scores are rendered as-is; missing inputs remain unresolved for cache\_busting, crawler\_governance, movement, origin\_impact, security\_evidence.

Reports use emitted artifact fields only. Missing evidence is unavailable, not zero or safe.
