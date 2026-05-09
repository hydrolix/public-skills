# Weekly Bot Posture Review \- www\.example\.com

Report type: `executive_posture`

Scope: www\.example\.com

## Executive Summary

Movement-only posture report based on emitted artifact fields. It does not infer cause.

## Metric Deltas

| Metric | Current | Baseline | Delta | Pct change | Direction | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| bot\_share\_pct | 34\.5 | 29 | 5\.5 | 18\.965517 | increase | high |
| rate\_429\_pct | 2\.1 | 0\.9 | 1\.2 | 120 | increase | high |
| requests | 1500000 | 1250000 | 250000 | 20 | increase | high |

## Top Scorecard Ranking

| Rank | Entity type | Entity | Score | Band | Primary domain | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | request\_host | www\.example\.com | 48 | medium\_review | cache\_busting | medium |

## Lens Rollup

Scorecard rollup uses emitted scorecard fields only; it does not create executive-only features.

### Domain Totals

| Domain | Total score |
| --- | --- |
| cache\_busting | 28 |
| crawler\_governance | 10 |
| origin\_impact | 10 |

### Primary Lens Counts

| Primary domain | Entities |
| --- | --- |
| cache\_busting | 1 |

### Caveats

| Caveat | Entities |
| --- | --- |
| feature\_input\_missing | 1 |

## Domain Score Matrix

| Entity | Total score | cache\_busting | crawler\_governance | origin\_impact |
| --- | --- | --- | --- | --- |
| www\.example\.com | 48 | 28 | 10 | 10 |

## Movers

| Value | Metric | Current | Baseline | Delta | Contribution pct | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| 64500 | requests | 420000 | 80000 | 340000 | 87\.179487 | high |
| 64600 | requests | 260000 | 210000 | 50000 | 12\.820513 | high |

## Analyst Notes

These notes are interpretive narrative, not facts strictly proven by artifact data alone.

### Note 1

_LLM interpretation._ Bot share and request volume both increased week\-over\-week; mover table attributes most of the growth to ASN 64500\.

- bot\_share\_pct delta: posture\-week\-1 /metrics/0/absolute\_delta = 5\.5
- top ASN contribution: mover\-asn\-1 /movers/0/contribution\_pct = 87\.179487

## Evidence Limits

### Artifact posture\-week\-1

- Schema: bot\_posture\_movement\.v1
- Table: bi\_summary\_day
- Scope: request\_host=www\.example\.com
- Confidence: unavailable
- Confidence reasons: unavailable
- Interpretation constraints: movement\_only, no\_causal\_claim, llm\_may\_summarize\_structured\_evidence\_only
- Windows: current\_window: \{"end": "2026\-04\-14","start": "2026\-04\-07"\}; baseline\_windows: \[\{"end": "2026\-04\-07","start": "2026\-03\-31"\}\]

### Artifact mover\-asn\-1

- Schema: bot\_mover\_attribution\.v1
- Table: bi\_summary\_day
- Scope: request\_host=www\.example\.com
- Confidence: unavailable
- Confidence reasons: unavailable
- Interpretation constraints: attribution\_from\_aggregate\_deltas, no\_causal\_claim, llm\_may\_summarize\_structured\_evidence\_only
- Windows: current\_window: \{"end": "2026\-04\-14","start": "2026\-04\-07"\}; baseline\_windows: \[\{"end": "2026\-04\-07","start": "2026\-03\-31"\}\]

### Artifact scorecards\-host\-1

- Schema: bot\_scorecard\_artifacts\.v1
- Table: unavailable
- Scope: unavailable
- Confidence: unavailable
- Confidence reasons: unavailable
- Interpretation constraints: unavailable

### Artifact scorecards\-host\-1\#index

- Schema: bot\_scorecard\_index\.v1
- Parent: scorecards\-host\-1 at /index
- Table: bi\_summary\_day
- Scope: request\_host=www\.example\.com
- Confidence: unavailable
- Confidence reasons: unavailable
- Interpretation constraints: unavailable
- Windows: current\_window: \{"end": "2026\-04\-14","start": "2026\-04\-07"\}; baseline\_windows: \[\{"end": "2026\-04\-07","start": "2026\-03\-31"\}\]

### Artifact scorecards\-host\-1\#scorecard\-1

- Schema: bot\_entity\_scorecard\.v1
- Parent: scorecards\-host\-1 at /scorecards/0
- Table: bi\_summary\_day
- Scope: request\_host=www\.example\.com
- Confidence: medium
- Confidence reasons: summary\_table\_used, retained\_dimensions\_fit, feature\_input\_missing
- Interpretation constraints: unavailable
- Windows: current\_window: \{"end": "2026\-04\-14","start": "2026\-04\-07"\}; baseline\_windows: \[\{"end": "2026\-04\-07","start": "2026\-03\-31"\}\]

Reports use emitted artifact fields only. Missing evidence is unavailable, not zero or safe.
