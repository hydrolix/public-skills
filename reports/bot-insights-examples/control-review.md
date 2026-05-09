# Control Review \- policy\-bot\-block\-1

Report type: `control_review`

Scope: www\.example\.com

## Control Review Summary

Effectiveness review based on emitted artifact fields. The artifact alone is not causal proof.

Target: \{"policy\_id": "policy\-bot\-block\-1"\}

Windows: before\_window: \{"end": "2026\-04\-01","start": "2026\-03\-25"\}; after\_window: \{"end": "2026\-04\-08","start": "2026\-04\-01"\}; expected\_window: \{"end": "2026\-04\-01","start": "2026\-03\-25"\}

## Before/After/Expected

| Metric | Before | After | Expected | Delta vs expected | Pct change | Status | Confidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| siem\_blocked\_requests | 90 | 280 | 100 | 180 | 180 | increased | high |

## Collateral Checks

| Metric | Before | After | Delta | Pct change | Status | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| rate\_429\_pct | 0\.4 | 2\.1 | unavailable | unavailable | increased | unavailable |

## Displacement Checks

| Metric | Before | After | Delta | Pct change | Status | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| requests | 1200000 | 1100000 | unavailable | unavailable | decreased | unavailable |

## Confidence

Expected basis: explicit\_target. This is an effectiveness review, not proof of cause.

## Evidence Limits

### Artifact control\-review\-1

- Schema: bot\_control\_review\.v1
- Table: bi\_siem\_policy\_summary\_day
- Scope: request\_host=www\.example\.com
- Confidence: unavailable
- Confidence reasons: unavailable
- Interpretation constraints: control\_effectiveness\_review, no\_causal\_claim\_without\_external\_change\_evidence, llm\_may\_summarize\_structured\_evidence\_only
- Windows: before\_window: \{"end": "2026\-04\-01","start": "2026\-03\-25"\}; after\_window: \{"end": "2026\-04\-08","start": "2026\-04\-01"\}; expected\_window: \{"end": "2026\-04\-01","start": "2026\-03\-25"\}

Reports use emitted artifact fields only. Missing evidence is unavailable, not zero or safe.
