# Anomaly Investigation — Mid-Morning Bot Share Spike

A worked example of using `bot-insights` for incident-style anomaly
investigation. The user starts with a vague observation; the agent
localizes the anomaly in time, decomposes it by entity, and discovers
the spike is actually two superimposed movers with very different
remediation paths.

This example is the counterpart to
[cross-window-capacity-plan.md](cross-window-capacity-plan.md) —
where that one was planning-style forward-looking analysis, this is
backward-looking incident triage. Different scripts, different
hypotheses, different framing.

Data is synthetic but the SQL, scripts, and artifact shapes match
the deployed skill.

This is an intentionally idealized worked conversation. It emphasizes the
target analysis flow over tool friction so readers can see what the skill can
help a user accomplish.

---

### User

> Bot share on `www.example.com` doubled this morning. Normal weekday
> baseline is around 28–32%, our dashboard hit 58% just before noon
> and stayed there. Akamai cluster. Can you tell me what happened
> and whether I need to do anything about it?

### Agent

Bot share doubling is multi-causal — could be a single new operator,
could be a verified crawler doing a large indexing run, could be
legitimate traffic dropping while bot traffic held steady. Before
naming a cause I want to localize the spike in time, then decompose
the peak hour by entity.

Loading the references I'll need.

```
Skill: open references/soc-analysis.md
       open references/summary-tables.md
```

`bi_summary_hour` has the grain I need for a same-day incident.
`bi_summary_day` would smear the spike across non-spike hours and
mislead the decomposition.

```sql
-- MCP: mcp__hydrolix__run_select_query
-- cluster: akamai
-- Hourly bot share for today, contextualized against the same hours
-- yesterday so the agent can see the inflection point clearly.
SELECT
  toStartOfHour(timestamp) AS hour,
  sum(requests)            AS requests,
  sum(bot_like_requests)   AS bot_requests,
  round(sum(bot_like_requests) / nullif(sum(requests), 0) * 100, 2) AS bot_share_pct
FROM akamai.bi_summary_hour
WHERE request_host = 'www.example.com'
  AND timestamp >= '2026-05-11 00:00:00'
  AND timestamp <  '2026-05-12 12:00:00'
GROUP BY hour
ORDER BY hour
```

```
Result (abridged — same-day pattern):
hour                | requests | bot_requests | bot_share_pct
2026-05-12 06:00    |  142,800 |    38,940    |   27.27
2026-05-12 07:00    |  168,420 |    47,210    |   28.03
2026-05-12 08:00    |  192,300 |    56,840    |   29.56
2026-05-12 09:00    |  220,140 |    66,300    |   30.12
2026-05-12 10:00    |  248,920 |   144,710    |   58.14   ← inflection
2026-05-12 11:00    |  263,400 |   152,800    |   58.01
2026-05-12 12:00 → on average 28% the previous day at the same hour
```

Sharp inflection at 10:00 UTC. Pre-10:00 traffic was at normal
weekday baseline (28–30%); from 10:00 onward bot share doubled and
held. The non-bot request volume kept growing slightly across the
window, so the spike is additive — new bot traffic appeared at 10:00,
real users were not driven away.

Total bot request growth at 10:00–11:00 vs. 08:00–09:00:
~205k additional bot requests in two hours. That's the budget the
movers have to fit inside.

### Agent

Decomposing the 10:00–11:00 peak by `client_asn`.

```sql
-- MCP: mcp__hydrolix__run_select_query
SELECT
  client_asn,
  sum(bot_like_requests)     AS bot_requests,
  sum(rate_limited_requests) AS rate_limited,
  countDistinct(toStartOfMinute(timestamp)) AS active_minutes
FROM akamai.bi_summary_hour
WHERE request_host = 'www.example.com'
  AND timestamp >= '2026-05-12 10:00:00'
  AND timestamp <  '2026-05-12 12:00:00'
GROUP BY client_asn
ORDER BY bot_requests DESC
LIMIT 10
```

```
Result:
client_asn | bot_requests | rate_limited | active_minutes
8075       |     104,820  |      120     |    120
64910      |      82,440  |    8,640     |     94
15169      |      18,100  |       40     |    120
32934      |      11,240  |       30     |    120
14618      |       9,800  |       20     |    120
…           …              …               …
```

Two movers, not one.

- **ASN 8075** (Microsoft / Bingbot range) — 104.8k requests, almost
  no rate limiting (120 / 104,820 = 0.11%), continuously active.
  This is the signature of a verified crawler doing an indexing
  burst, not an attack.
- **ASN 64910** (unknown commercial range) — 82.4k requests, 8,640
  rate-limited (10.5%), active only 94 of the 120 minutes.
  High rate-limit ratio + bursty pattern is the signature of an
  unverified automation operator probing for content.

Together they account for ~187k of the ~205k additional bot
requests, which fits the peak-hour budget. The other ASNs are at
their normal volumes.

I need to confirm 8075 is actually Bingbot (not someone spoofing
from a Microsoft IP range) and characterize 64910's risk
mechanically.

### Agent

Checking the `bot_class` and verified-bot signal alignment for both
ASNs at the peak window. If 8075 is real Bingbot, Akamai's enrichment
will classify it as `verified_bot` and align on bot detection.
Divergence between verified and unverified classification on the
same ASN is itself evidence to flag.

```sql
-- MCP: mcp__hydrolix__run_select_query
SELECT
  client_asn,
  bot_class,
  sum(requests)              AS requests,
  sum(bot_like_requests)     AS bot_requests
FROM akamai.bi_summary_hour
WHERE request_host = 'www.example.com'
  AND timestamp >= '2026-05-12 10:00:00'
  AND timestamp <  '2026-05-12 12:00:00'
  AND client_asn IN ('8075', '64910')
GROUP BY client_asn, bot_class
ORDER BY client_asn, bot_requests DESC
```

```
Result:
client_asn | bot_class                | requests | bot_requests
8075       | verified_search_crawler  | 104,720  | 104,720
8075       | unverified               |     180  |     100
64910      | unverified               |  82,720  |  82,440
64910      | suspected_bot            |   2,140  |   2,080
```

ASN 8075: 99.8% of its bot-like traffic is classified
`verified_search_crawler`. That's high alignment — not a spoof.

ASN 64910: 100% `unverified` or `suspected_bot`. No verified bot
classification at all.

Two very different remediation paths. I'll run a deterministic
scorecard on 64910 only, scoped to the SOC domain, so the user has
a structured artifact for the suspicious operator. 8075 doesn't
need a scorecard — the diagnosis is "legitimate crawler doing an
indexing burst."

### Agent

```sql
-- MCP: mcp__hydrolix__run_select_query
-- Aggregate the security_evidence inputs scorecard.py needs for
-- client_asn=64910 across the peak window, with the same dimensions
-- vs. a 24-hour pre-incident baseline (yesterday same window).
SELECT
  CASE
    WHEN timestamp >= '2026-05-12 10:00:00' AND timestamp < '2026-05-12 12:00:00' THEN 'current'
    WHEN timestamp >= '2026-05-11 10:00:00' AND timestamp < '2026-05-11 12:00:00' THEN 'baseline'
  END AS window_label,
  sum(requests)                       AS requests,
  sum(bot_like_requests)              AS bot_like_requests,
  sum(rate_limited_requests)          AS rate_429_requests,
  sum(if(bot_class = 'suspected_bot', bot_like_requests, 0)) AS bad_bot_requests,
  sum(siem_auth_fail_requests)        AS siem_auth_fail_requests,
  sum(siem_blocked_requests)          AS siem_blocked_requests,
  round(sum(bot_like_requests) / nullif(sum(requests), 0) * 100, 2) AS bot_share_pct,
  round(sum(if(bot_class = 'suspected_bot', bot_like_requests, 0))
        / nullif(sum(bot_like_requests), 0) * 100, 2) AS bad_bot_share_pct
FROM akamai.bi_siem_policy_summary_hour
WHERE request_host = 'www.example.com'
  AND client_asn = '64910'
  AND (
    (timestamp >= '2026-05-12 10:00:00' AND timestamp < '2026-05-12 12:00:00') OR
    (timestamp >= '2026-05-11 10:00:00' AND timestamp < '2026-05-11 12:00:00')
  )
GROUP BY window_label
```

```
Result:
window_label | requests | bot_like_requests | rate_429_requests | bad_bot_requests | siem_auth_fail_requests | siem_blocked_requests | bot_share_pct | bad_bot_share_pct
current      |  82,720  |       82,440      |       8,640       |       2,080      |          88             |      8,640            |    99.66      |     2.52
baseline     |   8,140  |        2,440      |          40       |          30      |           4             |         60            |    29.98      |     1.23
```

Two things to call out in this comparison. First, the SIEM evidence:
8,640 blocked requests at the peak vs. 60 at baseline — ~144× —
across a single ASN, scope-locked to one host and a 2-hour window.
88 auth failures across the same window reads as credential testing,
not casual scraping. Second, the bot share on this ASN itself went
from ~30% (mixed human/bot traffic) to ~99.7% (almost pure
automation). The ASN didn't just get busier; its composition
flipped.

Assembling the scorecard input.

```bash
# Bash: scorecard.py for the suspect ASN, SOC-domain only
cat <<'JSON' | uv run python skills/bot-insights/scripts/scorecard.py \
  --entity-type client_asn \
  --domains security_evidence,movement
{
  "metadata": {
    "table_used": "akamai.bi_siem_policy_summary_hour",
    "scope": {"request_host": "www.example.com", "cluster": "akamai"},
    "current_window":  {"start": "2026-05-12T10:00:00Z", "end": "2026-05-12T12:00:00Z"},
    "baseline_windows": [{"start": "2026-05-11T10:00:00Z", "end": "2026-05-11T12:00:00Z"}],
    "comparison_type": "same_hour_prior_day",
    "granularity": "hour"
  },
  "rows": [
    {
      "client_asn": "64910",
      "current_requests":         82720,
      "baseline_requests":         8140,
      "current_bot_like_requests": 82440,
      "baseline_bot_like_requests": 2440,
      "current_bot_share_pct":     99.66,
      "baseline_bot_share_pct":    29.98,
      "current_bad_bot_share_pct":  2.52,
      "baseline_bad_bot_share_pct": 1.23,
      "current_rate_429_requests":  8640,
      "baseline_rate_429_requests":   40,
      "current_siem_auth_fail_requests": 88,
      "baseline_siem_auth_fail_requests": 4,
      "current_siem_blocked_requests":  8640,
      "baseline_siem_blocked_requests":   60
    }
  ]
}
JSON
```

```json
{
  "schema_version": "bot_scorecard_artifacts.v1",
  "scorecards": [
    {
      "schema_version": "bot_entity_scorecard.v1",
      "entity_type": "client_asn",
      "entity": "64910",
      "score": 56,
      "band": "medium_review",
      "primary_domain": "security_evidence",
      "confidence": "medium",
      "confidence_reasons": [
        "summary_table_used",
        "retained_dimensions_fit",
        "current_count_sufficient",
        "baseline_count_sufficient",
        "siem_available"
      ],
      "rule_results": [
        {"domain": "security_evidence", "name": "siem_blocked_present",   "status": "triggered", "current": 8640, "threshold": 0,   "points": 12},
        {"domain": "security_evidence", "name": "siem_auth_fail_present", "status": "triggered", "current":   88, "threshold": 0,   "points": 12},
        {"domain": "movement",          "name": "volume_delta_high",      "status": "triggered", "current": 82720, "baseline": 8140, "threshold": 100, "points": 12},
        {"domain": "movement",          "name": "bot_share_delta_high",   "status": "triggered", "current": 99.66, "baseline": 29.98, "threshold": 10, "points": 8},
        {"domain": "security_evidence", "name": "bad_bot_share_high",     "status": "evaluated_zero", "current": 2.52, "threshold": 50}
      ],
      "interpretation_constraints": [
        "rule_based_scorecard",
        "mechanical_features_only",
        "no_causal_claim",
        "llm_may_summarize_structured_evidence_only"
      ]
    }
  ]
}
```

Score 56, medium-review band, primary domain `security_evidence`.
Four rules triggered: two SIEM rules (12 + 12 points), volume delta
(+12), and bot-share delta (+8) capturing the composition flip on
this ASN. The `bad_bot_share_high` rule did not trigger — most of
64910's traffic is `unverified`, not `suspected_bot`, so the Akamai
detector hasn't classified the behavior as malicious; the SIEM
policy did rate-limit and block, but the classifier hasn't
escalated. That divergence is worth flagging.

Confidence is medium rather than high because while the baseline
window has enough rows for the support check (`baseline_count_sufficient`),
the SIEM policy/detector classification is missing on 100% of
current-window traffic (no rule scored `bad_bot_share_high`
positively), so part of the evidence picture for severity is
unavailable. Not a problem on its own, but it's why the band is
medium-review, not high.

### Agent

Final check before I synthesize — are 8075 and 64910 hitting the
same paths or different paths? If 8075 is on `/sitemap.xml` and
crawl-friendly resources, it's a real indexing burst; if it's
hitting login pages or checkout, the verified classification is
suspect even with the right ASN. And if 64910 is on
`/api/v1/login` or `/api/v1/checkout`, the auth-failure count
gets a target.

```sql
-- MCP: mcp__hydrolix__run_select_query
SELECT
  client_asn,
  request_path_norm,
  sum(requests)             AS requests,
  sum(siem_auth_fail_requests) AS auth_failures
FROM akamai.bi_siem_policy_summary_hour
WHERE request_host = 'www.example.com'
  AND timestamp >= '2026-05-12 10:00:00'
  AND timestamp <  '2026-05-12 12:00:00'
  AND client_asn IN ('8075', '64910')
GROUP BY client_asn, request_path_norm
ORDER BY client_asn, requests DESC
LIMIT 20
```

```
Result (abridged, top 5 per ASN):
client_asn | request_path_norm   | requests | auth_failures
8075       | /sitemap.xml        |   12,440 |        0
8075       | /products/*         |   28,100 |        0
8075       | /articles/*         |   24,820 |        0
8075       | /                   |    8,940 |        0
8075       | /robots.txt         |       38 |        0
64910      | /api/v1/login       |   24,800 |       82
64910      | /api/v1/search      |   38,100 |        0
64910      | /api/v1/cart        |   12,440 |        4
64910      | /api/v1/checkout    |    4,920 |        2
64910      | /products/*         |    2,180 |        0
```

ASN 8075's path profile is exactly what a search indexer should
look like — sitemap, articles, product pages, root, robots.txt.
Zero auth failures. Verified classification is consistent with
behavior; no need to escalate.

ASN 64910's profile is consistent with credential testing
(`/api/v1/login` with 82 auth failures) layered onto search
scraping (`/api/v1/search` is the largest single path). Cart and
checkout requests are present but small — could be probing or
could be incidental session noise. The volume and the auth-fail
concentration on the login endpoint are the strong signals.

### Agent

**Diagnosis**

Bot share doubled at 10:00 UTC because of two unrelated movers:

1. **ASN 8075 (Microsoft / Bingbot)** — ~105k requests over two
   hours, 99.8% `verified_search_crawler`, path profile
   (sitemap.xml, product/article pages, robots.txt) consistent
   with a routine indexing burst. Zero auth failures.

2. **ASN 64910 (unverified)** — ~82k requests over two hours, 100%
   unverified or suspected-bot, 10.5% rate-limited, 88 SIEM auth
   failures concentrated on `/api/v1/login`. Scorecard band
   `medium_review` with `security_evidence` as the primary domain.

These are not the same incident. They happened to coincide in
time.

**Recommended action**

- **ASN 8075** — no action. Note in the SOC log that the spike was
  half attributable to a verified search crawler; if rate-limit
  alerting fires on this ASN during normal hours, adjust the
  threshold rather than blocking. Confirm `robots.txt` is current
  if there's any path you don't want indexed.

- **ASN 64910** — SOC investigate now. The credential-testing
  signal on `/api/v1/login` (82 auth failures in two hours from
  one ASN, baseline 2) plus 8,640 blocked requests is enough to
  warrant immediate triage. Recommended next:
  - Pull the user-agent distribution and unique IP count for ASN
    64910 over the same window — if it's a small number of IPs,
    block; if it's distributed, tighten rate-limit on
    `/api/v1/login` and monitor.
  - Check whether any logins from this ASN succeeded — if yes,
    those accounts need a forced password reset.

**Confidence: medium for the diagnosis, high for the routing.**

What the evidence supports:
- The two ASNs are distinct phenomena (different bot_class, different
  path profile, different SIEM signature).
- ASN 64910 is exhibiting credential-testing behavior on a single
  endpoint with measurable success rate (auth failures imply
  attempted authentications).
- ASN 8075's behavior is mechanically consistent with verified
  Bingbot.

What the evidence does **not** support:
- That ASN 8075 is actually Microsoft. ASN ownership lookup happens
  outside this skill; verified classification depends on Akamai's
  detector, which can be wrong. The behavior signature is the
  stronger evidence; trust it more than the ASN label.
- That ASN 64910's auth failures are credential stuffing specifically
  (vs. broken integration, vs. bug testing). The signal is
  consistent with credential testing; ruling out alternatives needs
  the user-agent and IP distribution.
- That blocking 64910 will end the incident — if the operator has
  multiple ASNs, blocking one shifts traffic, not eliminates it.

---

## What this example demonstrates

| Capability | Where it appears |
|---|---|
| Time-localization before decomposition | The agent runs hourly SQL first to find the 10:00 inflection, then decomposes only the peak window — not a 24-hour aggregate that would smear the signal. |
| Differentiating coincident movers | Two ASNs caused the spike. The agent does not aggregate them into one "the spike" — it characterizes each separately. |
| Choosing not to over-instrument | ASN 8075 is verified Bingbot with a clean path profile. The agent does **not** run a scorecard on it — there's nothing for the SOC domain to score. Predefined `soc_triage` would have scored both equally. |
| `scorecard.py` with a domain filter | `--domains security_evidence,movement` scopes the rule set to the SOC concerns instead of the default all-domain run. |
| SIEM-only evidence | The credential-testing diagnosis depends on `siem_auth_fail_requests` and `siem_blocked_requests` from `bi_siem_policy_summary_hour`. The agent only reaches for those once the unverified-classifier signal already concentrated suspicion. |
| Behavior > label rule | "ASN 8075 is verified" is treated as supporting evidence, not proof. Behavior signature (path profile, zero auth failures) carries the final claim. |
| Routing in the recommendation | The two findings get split recommendations with different urgency: one is "no action / monitor robots.txt," the other is "SOC triage now with specific next queries." |
| Explicit non-claims in the synthesis | The closing list names what cannot be claimed from this evidence — ASN ownership, intent specificity, sufficiency of single-ASN remediation. |
| Behavior-signature trust under spoof risk | The "what evidence does NOT support" section flags that even verified-bot ASN labels can be wrong, and tells the reader which signal to weight higher. |

## Related files

- [SKILL.md](../../SKILL.md) — data firewall, query guardrails
- [references/soc-analysis.md](../../references/soc-analysis.md) — SOC entity selection, scorecard inputs, spoofing-evidence patterns
- [references/scorecard-analysis.md](../../references/scorecard-analysis.md) — `scorecard.py` input shape, domain filters, confidence labeling
- [references/summary-tables.md](../../references/summary-tables.md) — choosing the right grain
- [scripts/scorecard.py](../../scripts/scorecard.py) — used above with `--entity-type client_asn --domains security_evidence,movement`
- [cross-window-capacity-plan.md](cross-window-capacity-plan.md) — companion example for planning-style cross-window comparison
