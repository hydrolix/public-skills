# SIEM Unavailable Triage — Suspicious ASN Without Security Evidence

A worked example of using `bot-insights` when the investigation is useful but
not conclusive. The user asks whether traffic is malicious, but the available
cluster only has posture summaries. The agent still narrows the scope and
routes follow-up work, while clearly avoiding SOC claims that require SIEM
evidence.

This example complements [anomaly-investigation.md](anomaly-investigation.md):
that sample has SIEM evidence and can recommend SOC triage now; this one shows
how the skill behaves when the security-evidence layer is not available.

Data is synthetic but the SQL, scripts, and artifact shapes match the deployed
skill.

This is an intentionally idealized worked conversation. It emphasizes the
target analysis flow over tool friction so readers can see what the skill can
help a user accomplish.

---

### User

> We saw a big bot spike from one ASN on `shop.example.com` yesterday.
> I need to know if it was malicious or just noisy scraping. Cluster is
> Akamai. Can you check?

### Agent

I can check whether the traffic looks operationally risky, but I need to be
careful about the word "malicious." That requires security evidence such as
policy actions, auth failures, blocked requests, or detector confidence. I'll
first confirm what Bot Insights summaries are available, then use the best
retained grain.

Loading the references I'll need.

```
Skill: open references/summary-tables.md
       open references/soc-analysis.md
       open references/scorecard-analysis.md
```

The posture summaries are present, but SIEM policy summaries are not deployed
for this cluster. That means I can analyze movement, bot share, 429s, cache
pressure, and entity concentration. I cannot prove attack activity or
credential testing from this data alone.

```sql
-- MCP: mcp__hydrolix__run_select_query
-- cluster: akamai
-- Check whether SIEM policy summaries are available before using SOC-only
-- evidence fields.
SHOW TABLES FROM akamai LIKE 'bi_siem_policy_summary_%'
```

```
Result:
no rows
```

I'll proceed with posture summaries and keep the output framed as operational
risk, not maliciousness.

### Agent

First, compare yesterday to the prior weekday for the host.

```sql
-- MCP: mcp__hydrolix__run_select_query
SELECT
  CASE
    WHEN timestamp >= '2026-05-10 00:00:00' AND timestamp < '2026-05-11 00:00:00' THEN 'current'
    WHEN timestamp >= '2026-05-03 00:00:00' AND timestamp < '2026-05-04 00:00:00' THEN 'baseline'
  END AS window_label,
  sum(requests)              AS requests,
  sum(bot_like_requests)     AS bot_requests,
  sum(rate_limited_requests) AS rate_429_requests,
  sum(cache_miss_requests)   AS cache_miss_requests,
  round(sum(bot_like_requests) / nullif(sum(requests), 0) * 100, 2) AS bot_share_pct,
  round(sum(rate_limited_requests) / nullif(sum(requests), 0) * 100, 2) AS rate_429_pct,
  round(sum(cache_miss_requests) / nullif(sum(requests), 0) * 100, 2) AS cache_miss_pct
FROM akamai.bi_summary_day
WHERE request_host = 'shop.example.com'
  AND (
    (timestamp >= '2026-05-10 00:00:00' AND timestamp < '2026-05-11 00:00:00') OR
    (timestamp >= '2026-05-03 00:00:00' AND timestamp < '2026-05-04 00:00:00')
  )
GROUP BY window_label
ORDER BY window_label
```

```
Result:
window_label | requests  | bot_requests | rate_429_requests | cache_miss_requests | bot_share_pct | rate_429_pct | cache_miss_pct
baseline     | 1,920,000 |   518,400    |      4,900        |       282,000       |    27.00      |    0.26      |    14.69
current      | 2,740,000 | 1,287,800    |     58,600        |       794,600       |    47.00      |    2.14      |    29.00
```

Yesterday was materially different: request volume was up 43%, bot share was
up 20 percentage points, 429 rate was roughly 8x higher, and cache-miss rate
nearly doubled. That is enough to justify an operational investigation even
without SIEM.

Next I need to see whether the movement is concentrated in one ASN or broad
across many sources.

### Agent

```sql
-- MCP: mcp__hydrolix__run_select_query
SELECT
  client_asn,
  sum(bot_like_requests)     AS bot_requests,
  sum(rate_limited_requests) AS rate_429_requests,
  sum(cache_miss_requests)   AS cache_miss_requests,
  round(sum(rate_limited_requests) / nullif(sum(requests), 0) * 100, 2) AS rate_429_pct,
  round(sum(cache_miss_requests) / nullif(sum(requests), 0) * 100, 2) AS cache_miss_pct
FROM akamai.bi_summary_day
WHERE request_host = 'shop.example.com'
  AND timestamp >= '2026-05-10 00:00:00'
  AND timestamp <  '2026-05-11 00:00:00'
GROUP BY client_asn
ORDER BY bot_requests DESC
LIMIT 10
```

```
Result:
client_asn | bot_requests | rate_429_requests | cache_miss_requests | rate_429_pct | cache_miss_pct
64590      |   612,400    |      41,200       |      418,900        |    5.80      |    58.90
15169      |   156,200    |         340       |       18,600        |    0.08      |     4.50
8075       |   132,900    |         280       |       22,100        |    0.10      |     7.80
32934      |    98,400    |         190       |       12,700        |    0.07      |     4.60
64620      |    74,300    |       5,900       |       61,400        |    4.30      |    44.80
```

The issue is concentrated. ASN 64590 alone accounts for roughly half of all
bot requests and most of the 429/cache-miss pressure. The verified crawler
ranges in the same top list have low 429 and low miss rates, so they are not
the operational problem.

I still cannot call 64590 malicious. What I can say is that it is the primary
operational-risk entity in this window.

### Agent

I'll run a scorecard scoped to movement, cache/origin pressure, and security
evidence. The security rules should remain missing-input evidence because SIEM
fields are unavailable; missing security evidence must not be scored as safe.

```bash
cat <<'JSON' | uv run python skills/bot-insights/scripts/scorecard.py \
  --entity-type client_asn \
  --domains movement,cache_busting,origin_impact,security_evidence
{
  "table_used": "akamai.bi_summary_day",
  "scope": {"request_host": "shop.example.com", "cluster": "akamai"},
  "current_window": {"start": "2026-05-10T00:00:00Z", "end": "2026-05-11T00:00:00Z"},
  "baseline_windows": [{"start": "2026-05-03T00:00:00Z", "end": "2026-05-04T00:00:00Z"}],
  "comparison_type": "same_weekday_prior_week",
  "granularity": "day",
  "summary_table_used": true,
  "source_caveats": ["siem_unavailable"],
  "rows": [
    {
      "client_asn": "64590",
      "current_requests": 710000,
      "baseline_requests": 84000,
      "current_bot_like_requests": 612400,
      "baseline_bot_like_requests": 70400,
      "current_bot_share_pct": 86.25,
      "baseline_bot_share_pct": 83.81,
      "current_rate_429_pct": 5.80,
      "baseline_rate_429_pct": 0.31,
      "current_cache_miss_pct": 58.90,
      "baseline_cache_miss_pct": 18.40,
      "current_qs_diversity_ratio": 0.62,
      "baseline_qs_diversity_ratio": 0.08
    }
  ]
}
JSON
```

Result, abridged to the relevant scorecard fields:

```json
{
  "schema_version": "bot_scorecard_artifacts.v1",
  "scorecards": [
    {
      "schema_version": "bot_entity_scorecard.v1",
      "entity_type": "client_asn",
      "entity": "64590",
      "score": 36,
      "band": "high_review",
      "primary_domain": "cache_busting",
      "confidence": "medium",
      "confidence_reasons": [
        "summary_table_used",
        "retained_dimensions_fit",
        "current_count_sufficient",
        "baseline_count_sufficient",
        "source_coverage_caveat",
        "siem_unavailable",
        "feature_input_missing"
      ],
      "rule_results": [
        {"domain": "movement", "name": "volume_delta_high", "status": "triggered", "points": 12},
        {"domain": "cache_busting", "name": "cache_miss_delta_high", "status": "triggered", "points": 8},
        {"domain": "cache_busting", "name": "cache_miss_rate_high", "status": "triggered", "points": 10},
        {"domain": "cache_busting", "name": "querystring_diversity_high", "status": "triggered", "points": 16},
        {"domain": "cache_busting", "name": "querystring_diversity_with_high_miss_rate", "status": "triggered", "points": 18},
        {"domain": "security_evidence", "name": "siem_auth_fail_present", "status": "missing_input", "points": 0},
        {"domain": "security_evidence", "name": "siem_blocked_present", "status": "missing_input", "points": 0}
      ],
      "not_evaluated_features": [
        {
          "domain": "security_evidence",
          "name": "siem_blocked_present",
          "reason": "feature_input_missing",
          "missing_inputs": ["siem_blocked_requests"]
        },
        {
          "domain": "security_evidence",
          "name": "siem_auth_fail_present",
          "reason": "feature_input_missing",
          "missing_inputs": ["siem_auth_fail_requests"]
        }
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

The scorecard confirms the operational read: high-review, driven by cache
busting and volume movement. Security evidence remains missing input, not a
clean bill of health. The artifact does not establish security intent.

### Agent

**Short answer**

I would not label this malicious from Bot Insights posture data alone. I would
treat it as a high-priority operational bot issue and route it to Edge/Ops or
the bot-control owner for cache and rate-limit review.

**What happened**

ASN 64590 drove the spike on `shop.example.com`:

- 612k bot-like requests in one day, up from ~70k the prior same weekday.
- 5.8% 429 rate, compared with 0.31% at baseline.
- 58.9% cache-miss rate, compared with 18.4% at baseline.
- Query-string diversity rose sharply, which is consistent with cache-key
  churn or structured scraping.

Verified crawler ASNs were also present, but their 429 and cache-miss rates
were low. They are not the main operational risk in this window.

**What I can say confidently**

- This is not broad organic growth; it is concentrated in ASN 64590.
- The traffic is operationally expensive because it combines high bot volume,
  high cache misses, and elevated 429s.
- The right immediate owner is Edge/Ops or bot-control policy, not necessarily
  SOC.

**What I cannot say from this evidence**

- I cannot prove malicious intent.
- I cannot say whether auth attacks occurred.
- I cannot say whether requests were blocked by security policy.
- I cannot distinguish credential stuffing from scraper behavior without SIEM,
  WAF, auth, or application logs.

**Recommended next steps**

1. Pull path-grain posture or CDN logs for ASN 64590 and identify the top
   cache-miss paths and query-string patterns.
2. Check WAF/auth logs for the same ASN and window before escalating as a SOC
   incident.
3. If the traffic concentrates on cacheable catalog/search pages, tune
   cache-key normalization or rate limits before blocking.
4. If WAF/auth logs show login attempts or denied security actions, re-run the
   investigation as SOC triage with SIEM evidence.

---

## What this example demonstrates

| Capability | Where it appears |
|---|---|
| Graceful fallback when SIEM is unavailable | The agent checks for SIEM summaries, finds none, and switches to posture-only analysis instead of fabricating SOC evidence. |
| Clear evidence boundary | The final answer separates operational bot risk from malicious intent. |
| Domain-scoped scorecard use | `scorecard.py` runs with movement/cache/origin domains, excluding security evidence that cannot be evaluated. |
| Missing evidence is not safe evidence | The scorecard records SIEM features as not evaluated rather than scoring them as clean. |
| Useful incomplete answer | The agent still identifies an owner, a likely operational mechanism, and concrete next checks. |

## Related files

- [SKILL.md](../../SKILL.md) — data firewall, query guardrails, deployment-availability rule
- [references/summary-tables.md](../../references/summary-tables.md) — retained dimensions and SIEM availability
- [references/scorecard-analysis.md](../../references/scorecard-analysis.md) — domain filters and missing-feature behavior
- [references/cache-origin-impact.md](../../references/cache-origin-impact.md) — cache-busting and origin-pressure patterns
