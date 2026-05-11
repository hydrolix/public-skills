# Cross-Window Capacity Plan — Black Friday vs. Last Weekend

A worked example of using `bot-insights` for analysis the predefined report
types do not support. The user wants to compare two arbitrary,
non-adjacent windows; none of `executive_posture`, `control_review`,
`soc_triage`, `scorecard_brief`, `crawler_governance`, or
`edge_ops_impact` accepts non-adjacent baselines (see
[scenarios/05-year-ago-baseline-window.md](../../scenarios/05-year-ago-baseline-window.md)).
The agent assembles a structured movement artifact from ad-hoc MCP
queries and the artifact-only `compare_posture.py` script.

The data and identifiers are synthetic but the SQL, script invocations,
and artifact shapes match the deployed skill.

This is an intentionally idealized worked conversation. It emphasizes the
target analysis flow over tool friction so readers can see what the skill can
help a user accomplish.

---

### User

> We're planning capacity for Memorial Day weekend (May 23–25). Last
> Black Friday (Nov 28–30, 2025) we got slammed — bot traffic surged
> and 429s spiked. This past weekend (May 9–11) felt normal. Compare
> the two so I know how much of last BF was holiday-specific vs. a
> baseline we should expect again. Cluster is Akamai, host is
> `www.example.com`.

### Agent

I'll plan this before running anything. No predefined report fits —
the windows are non-adjacent and one is in a different calendar year,
so `executive_posture` (current vs. previous-period) and
`control_review` (before/after a control event) are both wrong shape.
I'll need ad-hoc SQL to bracket the two windows, then feed the result
into `compare_posture.py` to get a structured movement artifact.

Loading the relevant references first.

```
Skill: open references/baseline-comparison.md
       open references/summary-tables.md
       open references/data-model.md
```

`bi_summary_day` retains `request_host`, `client_asn`, `bot_class`,
counts including `requests`, `bot_like_requests`,
`rate_limited_requests`. The grain fits — three-day windows, daily
buckets, host scope. I'll start with the headline metrics and only
break down by ASN if the totals are interesting.

```sql
-- MCP: mcp__hydrolix__run_select_query
-- cluster: akamai
SELECT
  CASE
    WHEN timestamp >= '2025-11-28' AND timestamp < '2025-12-01' THEN 'bf_2025'
    WHEN timestamp >= '2026-05-09' AND timestamp < '2026-05-12' THEN 'last_weekend'
  END AS window_label,
  sum(requests)                                                  AS requests,
  sum(bot_like_requests)                                         AS bot_requests,
  sum(rate_limited_requests)                                     AS rate_limited,
  round(sum(bot_like_requests) / nullif(sum(requests), 0) * 100, 2)     AS bot_share_pct,
  round(sum(rate_limited_requests) / nullif(sum(requests), 0) * 100, 2) AS rate_429_pct
FROM akamai.bi_summary_day
WHERE request_host = 'www.example.com'
  AND (
    (timestamp >= '2025-11-28' AND timestamp < '2025-12-01') OR
    (timestamp >= '2026-05-09' AND timestamp < '2026-05-12')
  )
GROUP BY window_label
ORDER BY window_label
```

```
Result:
window_label  | requests   | bot_requests | rate_limited | bot_share_pct | rate_429_pct
bf_2025       | 14,820,000 | 6,402,240    | 280,140      | 43.20         | 1.89
last_weekend  |  4,108,000 | 1,203,644    |  18,068      | 29.30         | 0.44
```

Baseline established. Black Friday was 3.6× the total volume, bot
share was +13.9 percentage points higher, and the 429 rate was 4.3×
higher. The question now is *who* drove the BF surge — if it
concentrates in a few automation operators we can characterize,
that's actionable; if it's broad, the answer is closer to "the whole
internet got busy."

### Agent

```sql
-- MCP: mcp__hydrolix__run_select_query
-- top 10 ASNs by bot_like_requests in each window
SELECT
  CASE
    WHEN timestamp >= '2025-11-28' AND timestamp < '2025-12-01' THEN 'bf_2025'
    WHEN timestamp >= '2026-05-09' AND timestamp < '2026-05-12' THEN 'last_weekend'
  END AS window_label,
  client_asn,
  sum(bot_like_requests)     AS bot_requests,
  sum(rate_limited_requests) AS rate_limited
FROM akamai.bi_summary_day
WHERE request_host = 'www.example.com'
  AND (
    (timestamp >= '2025-11-28' AND timestamp < '2025-12-01') OR
    (timestamp >= '2026-05-09' AND timestamp < '2026-05-12')
  )
GROUP BY window_label, client_asn
ORDER BY window_label, bot_requests DESC
LIMIT 20
```

```
Result (abridged to top 5 per window):
window_label  | client_asn | bot_requests | rate_limited
bf_2025       | 64500      | 2,920,400    | 142,800
bf_2025       | 64200      | 1,810,360    |  78,440
bf_2025       | 64710      |   430,180    |   8,220
bf_2025       | 32934      |   210,440    |     580
bf_2025       | 15169      |   168,090    |     310
last_weekend  | 32934      |   188,100    |     720
last_weekend  | 15169      |   164,800    |     420
last_weekend  | 8075       |    98,440    |     180
last_weekend  | 64500      |    31,200    |     140
last_weekend  | 14618      |    27,910    |     110
```

Top two BF ASNs — 64500 and 64200 — account for 4.73M of 6.40M bot
requests on BF (74%) and 221k of 280k 429s (79%). Neither dominates
last weekend; 64500 is present but at ~1% of its BF volume, and
64200 doesn't appear in the top 10 at all. The ASNs that DO dominate
last weekend (32934, 15169 — Facebook and Google search crawler
ranges) are present in the BF top 5 but at far lower volume.

That's a strong "holiday-specific" signal, but I want to confirm
64500 and 64200 weren't simply throttled off the platform after BF.
If they had been blocked, capacity planning would treat their
absence as policy-enforced; if they're seasonally inactive,
capacity planning has to assume they could return.

### Agent

I'll feed the structured posture comparison through `compare_posture.py`
to get an artifact with deltas, contribution percentages, and
confidence reasons computed deterministically — instead of doing
that math in prose.

```bash
# Bash: assemble the input JSON inline so the script gets the exact
# rows it expects (current_requests / baseline_requests per mover)
cat <<'JSON' | uv run python skills/bot-insights/scripts/compare_posture.py --schema movers
{
  "metadata": {
    "table_used": "akamai.bi_summary_day",
    "scope": {"request_host": "www.example.com", "cluster": "akamai"},
    "current_window":  {"start": "2025-11-28", "end": "2025-12-01", "label": "bf_2025"},
    "baseline_window": {"start": "2026-05-09", "end": "2026-05-12", "label": "last_weekend"},
    "comparison_type": "explicit_target",
    "granularity": "day",
    "dimension": "client_asn",
    "metric": "bot_like_requests"
  },
  "rows": [
    {"value": "64500", "current": 2920400, "baseline":   31200},
    {"value": "64200", "current": 1810360, "baseline":       0},
    {"value": "64710", "current":  430180, "baseline":   12440},
    {"value": "32934", "current":  210440, "baseline":  188100},
    {"value": "15169", "current":  168090, "baseline":  164800},
    {"value": "8075",  "current":   24800, "baseline":   98440}
  ]
}
JSON
```

```json
{
  "schema_version": "bot_mover_attribution.v1",
  "artifact_id": "compare-posture-movers-1",
  "table_used": "akamai.bi_summary_day",
  "scope": {"request_host": "www.example.com", "cluster": "akamai"},
  "current_window":  {"start": "2025-11-28", "end": "2025-12-01"},
  "baseline_windows": [{"start": "2026-05-09", "end": "2026-05-12"}],
  "comparison_type": "explicit_target",
  "granularity": "day",
  "dimension": "client_asn",
  "metric": "bot_like_requests",
  "movers": [
    {"value": "64500", "current": 2920400, "baseline":  31200, "absolute_delta": 2889200, "pct_change": 9260.26, "direction": "increase", "contribution_pct": 56.46, "confidence": "high"},
    {"value": "64200", "current": 1810360, "baseline":      0, "absolute_delta": 1810360, "pct_change": null,    "direction": "new",      "contribution_pct": 35.38, "confidence": "high"},
    {"value": "64710", "current":  430180, "baseline":  12440, "absolute_delta":  417740, "pct_change": 3358.04, "direction": "increase", "contribution_pct":  8.16, "confidence": "high"},
    {"value": "32934", "current":  210440, "baseline": 188100, "absolute_delta":   22340, "pct_change":   11.88, "direction": "increase", "contribution_pct":  0.44, "confidence": "high"},
    {"value": "15169", "current":  168090, "baseline": 164800, "absolute_delta":    3290, "pct_change":    2.00, "direction": "stable",   "contribution_pct":  0.06, "confidence": "high"},
    {"value": "8075",  "current":   24800, "baseline":  98440, "absolute_delta":  -73640, "pct_change":  -74.81, "direction": "decrease", "contribution_pct":  1.44, "confidence": "high"}
  ],
  "total_delta": 5116970,
  "total_delta_basis": "sum_abs_mover_delta",
  "interpretation_constraints": [
    "attribution_from_aggregate_deltas",
    "no_causal_claim",
    "llm_may_summarize_structured_evidence_only"
  ]
}
```

Three ASNs (64500, 64200, 64710) account for 99.99% of the
absolute-delta basis. 32934 and 15169 — the well-known search crawler
ranges — barely moved between the two windows. So the BF surge is
not "the whole internet"; it's three commercial operators.

To distinguish "seasonal" from "throttled off," I need to look at
64500 and 64200's daily activity over the weeks between the two
windows. If they trail off after BF and stay flat, that's seasonal.
If they have a sudden cliff, that's enforcement.

```sql
-- MCP: mcp__hydrolix__run_select_query
-- daily bot_requests + 429s for the two BF-dominant ASNs,
-- across the four weeks leading up to last weekend
SELECT
  toDate(timestamp) AS day,
  client_asn,
  sum(bot_like_requests)     AS bot_requests,
  sum(rate_limited_requests) AS rate_limited
FROM akamai.bi_summary_day
WHERE request_host = 'www.example.com'
  AND client_asn IN ('64500', '64200')
  AND timestamp >= '2026-04-12'
  AND timestamp <  '2026-05-12'
GROUP BY day, client_asn
ORDER BY day, client_asn
```

```
Result (sampled — 30 daily rows; pattern):
2026-04-12  | 64200 |    18,840 |  82
2026-04-12  | 64500 |    24,180 | 110
2026-04-13  | 64200 |    19,210 |  74
2026-04-13  | 64500 |    25,900 | 124
…           …      …            …
2026-05-10  | 64200 |    20,440 |  88
2026-05-10  | 64500 |    23,610 | 105
2026-05-11  | 64200 |    21,300 |  91
2026-05-11  | 64500 |    25,140 | 118

(no missing days; daily means: 64500 ≈ 24K, 64200 ≈ 19K;
 daily stddev / mean < 10% for both)
```

Both ASNs are at flat low-volume baselines across the entire
intervening period — no cliff, no decay. That's consistent with
seasonal pre-holiday activity, not policy enforcement. Capacity
planning should treat them as latent: present at ~20–25k/day each
in normal weeks, capable of reaching 1–3M/day during a commercial
peak.

### Agent

**Memorial Day capacity plan**

Black Friday 2025 was not a normal baseline — two commercial scraper
ASNs (64500, 64200) drove 92% of the bot-like-request increase and
79% of the 429 surge. Both have been at flat low-volume baselines
(~20–25k bot requests/day each) for the four weeks leading up to
last weekend, consistent with seasonal pre-holiday activity rather
than year-round traffic. Verified-bot ASNs (Google 15169, Facebook
32934) barely changed between windows.

**Confidence: medium.**

What the evidence supports:
- The *shape* of a Memorial Day surge, if it happens, will likely
  resemble BF: a small number of commercial scrapers driving most of
  the bot delta on a small number of high-value paths.
- ASNs 64500 and 64200 are still active at low volume, so any
  capacity model that assumes them gone is unsafe.
- The 429 surge tracks the bot surge ASN-for-ASN — these operators
  are the ones being rate-limited.

What the evidence does **not** support:
- That Memorial Day magnitude will match BF. Memorial Day is a
  smaller commercial event; the historical pattern from this data
  alone can't fix the multiplier. Pulling Memorial Day 2025
  (May 24–26) as a separate comparison would tighten this — that's
  the next query if you want a number.
- Per-path impact. This analysis grouped on `request_host` and
  `client_asn` only. If the BF 429s concentrated on `/pricing` or
  `/inventory`, those paths warrant pre-warming or rate-limit
  rule review; that needs path-grain SQL or a `cache_origin_impact`
  artifact.
- Whether either scraper has been blocked since I last looked at
  the data. The evidence is movement-only; no causal claim about
  enforcement.

**Recommended next steps:**

1. Pull Memorial Day 2025 (May 24–26) and run the same comparison
   against this past weekend to fix the multiplier.
2. Path-grain query for the BF window — group by `path` on the
   top-2 ASNs to identify the targets for rate-limit tuning.
3. Confirm with edge-ops that the 64500/64200 BF-era rate-limit
   policies are still in effect before assuming "the same controls
   will hold."

---

## What this example demonstrates

| Capability | Where it appears |
|---|---|
| Cross-window analysis the CLI does not support natively | The first SQL block brackets two non-adjacent windows in a single query — `bot_insights_report.py --baseline-start` would not produce this comparison. |
| Loading references on demand | `Skill: open references/...` at the start, only the ones relevant to baseline reasoning and summary-table selection. |
| Ad-hoc SQL via the MCP path | Three independent `run_select_query` calls — allowed because none of these are predefined report types (see SKILL.md Data Firewall). |
| Re-using artifact scripts on hand-assembled JSON | `compare_posture.py --schema movers` consumes JSON the agent built from query results — no Hydrolix connectivity. |
| Deterministic delta/contribution math | Movement and contribution percentages come from `compare_posture.py`, not from the agent's prose. |
| Drill-down driven by an explicit hypothesis | The "seasonal vs. throttled" check is a follow-up query the agent justifies before running, not a generic "let me also look at…" |
| Explicit confidence framing | Final synthesis names what the evidence supports, what it does not, and what would tighten the answer. No causal claims. |
| Treating reader-facing labels as labels | The agent refers to "bot share," "429 rate," "ASN," and "verified-bot crawlers" — not `bot_share_pct`, `rate_429_pct`, `client_asn`, `bot_class=verified`. |

## Related files

- [SKILL.md](../../SKILL.md) — routing, data firewall, query guardrails
- [references/baseline-comparison.md](../../references/baseline-comparison.md) — comparison_type taxonomy, delta math
- [references/summary-tables.md](../../references/summary-tables.md) — retained dimensions
- [scripts/compare_posture.py](../../scripts/compare_posture.py) — `--schema movers` mode used above
- [scenarios/05-year-ago-baseline-window.md](../../scenarios/05-year-ago-baseline-window.md) — why the CLI does not accept non-adjacent baselines and what to do instead
