---
name: bot-insights
description: Use when investigating bot scoring, classification confidence, bot intent, verified bot ownership, attack data, crawler governance, bot-driven CDN impact, suspicious automation, SIEM/spoofing evidence, control or policy-change review, protected-population collateral, entity scorecards, saved Bot Insights report artifacts, bot-share spikes, 429 surges, automation incidents or anomalies, cross-window comparisons, or capacity planning against historical bot traffic.
license: Apache-2.0
metadata:
  version: 1.1.0
  author: Hydrolix
  bundle: bot-insights
---

# Bot Insights Analysis

Routing layer for investigating bot behavior in the Hydrolix `bot-insights`
bundle. This file is a router, not a manual — pick one reference below and
load only that file.

Compatible with Claude- and Codex-style skill loading: standard frontmatter,
relative markdown references, no agent-specific tool assumptions.

## When

Use when the user asks about:

- Bot traffic share, scoring, class, confidence, intent, or producer.
- Verified/unverified bot ownership, crawler health, AI crawler activity.
- Suspicious crawler spoofing, residential bot traffic, attack evidence, or
  Akamai SIEM bot/security enrichment.
- Bot-driven cache misses, query-string churn, origin load, latency,
  bandwidth, or rate-limiting impact.
- Before/after checks for blocks, cache-key changes, rate limits, bot-control
  policies, or security-policy changes.
- Protected-population collateral or displacement after a mitigation.
- Bot posture across domains, hosts, ASNs, paths, countries, or CDN sources.
- Entity prioritization, deterministic scorecards, ranked investigation
  packets.

## Core Principle

Bot enrichment plus access logs lets you connect automation identity to
operational impact. Stay evidence-first: never classify traffic from a single
signal; prefer deltas, source attribution, behavior, and impact over raw
top-N volume.

## Deployed Surfaces

| Family | Granularity | Availability |
|---|---|---|
| `bi_summary_*` | minute / hour / day | Every Bot Insights cluster |
| `bi_siem_policy_summary_*` | minute / hour / day | SIEM-enabled clusters only (e.g. `demo.trafficpeak.live`) |

For the `demo.trafficpeak.live` Akamai project, qualify as
`akamai.bi_summary_*` and `akamai.bi_siem_policy_summary_*`. Confirm SIEM
data exists for the target cluster before composing SIEM-only queries; SOC
reports fall back to posture summaries when SIEM is absent.

Older skill iterations referenced request-level tables (`bot_detection`,
`bot_detection_siem`) and focused aggregate families (`bot_agg_*`). Those are
**not deployed** on observed clusters today — treat them as design-intent
reference only and do not generate SQL against them.

**Deployment-availability rule.** When a question needs a dimension not
retained in `bi_summary_*` or `bi_siem_policy_summary_*`, state the
limitation in the artifact rather than substituting a non-deployed table.
References cite this as "the deployment-availability rule (SKILL.md)".

Field listings, canonical-vs-source-style aliases, and persona definitions
live in [references/data-model.md](references/data-model.md). Inspect table
metadata before querying and normalize back to canonical names expected by
deterministic scripts.

## Progressive Disclosure

Do not read every reference at startup. Pick the smallest relevant file:

- Table shape, fields, personas → [references/data-model.md](references/data-model.md)
- Summary inventory, retained dimensions, selection → [references/summary-tables.md](references/summary-tables.md)
- Live TrafficPeak demo cluster + Akamai project → [references/trafficpeak-demo.md](references/trafficpeak-demo.md)
- QoQ/MoM/WoW/YoY, seasonal, control-review baselines → [references/baseline-comparison.md](references/baseline-comparison.md)
- Protected-population collateral, displacement → [references/policy-collateral-analysis.md](references/policy-collateral-analysis.md)
- Full column inventory, flags, suppressed fields → [references/schema.md](references/schema.md)
- SOC, deltas, spoofing, attack evidence → [references/soc-analysis.md](references/soc-analysis.md)
- SEO, good-bot governance, verified/AI crawlers → [references/seo-analysis.md](references/seo-analysis.md)
- Structured cache-busting, miss movement, origin impact → [references/cache-origin-impact.md](references/cache-origin-impact.md)
- Broader Edge/Ops cache, origin, bandwidth → [references/edge-ops-analysis.md](references/edge-ops-analysis.md)
- Deterministic entity scorecards → [references/scorecard-analysis.md](references/scorecard-analysis.md)
- Advanced aggregate-delta attribution CLI → [references/advanced-attribution.md](references/advanced-attribution.md)
- Executive posture, multi-domain triage, mitigation verification → [references/executive-analysis.md](references/executive-analysis.md)
- Rendering saved artifacts to Markdown/HTML → [references/reporting.md](references/reporting.md)
- Runnable report-rendering demo payloads → [examples/](examples/)
- Known schema and analysis footguns → [references/pitfalls.md](references/pitfalls.md)
- Worked conversation examples for non-predefined-report workflows → [examples/conversations/](examples/conversations/)
- Documented failure modes, pressure scenarios, and how to re-run them → [scenarios/](scenarios/)

## Analysis Routing

| User intent | Load | Deterministic output |
|---|---|---|
| Reproduce live TrafficPeak demo dashboards | `trafficpeak-demo.md` | Summary-first SQL on `akamai.bi_*` |
| What changed over a baseline? | `baseline-comparison.md` | `bot_posture_movement.v1` |
| Which entity drove movement? | `baseline-comparison.md` (+ `advanced-attribution.md` for aggregate-delta) | `bot_mover_attribution.v1` / `bot_attribution_report.v1` |
| Did a mitigation work? | `baseline-comparison.md` | `bot_control_review.v1` |
| Did a policy hurt protected traffic? | `policy-collateral-analysis.md` (+ `scorecard-analysis.md` / `advanced-attribution.md`) | collateral / displacement / `policy_collateral` features |
| SOC / SIEM / spoofing / attack evidence | `soc-analysis.md` | Summary-backed SOC queries or scorecard rows |
| Crawler availability / AI crawler governance | `seo-analysis.md` | SEO query patterns or scorecard rows |
| Cache busting / origin pressure | `cache-origin-impact.md` (structured); else `edge-ops-analysis.md` | `cache_origin_impact_report.v1` |
| Executive posture, multi-team routing | `executive-analysis.md` (+ `reporting.md`) | Executive artifacts and rendered reports |
| Rank entities for handoff | `scorecard-analysis.md` | `bot_entity_scorecard.v1`, `bot_scorecard_index.v1` |
| Render saved artifacts | `reporting.md` | Markdown or self-contained HTML |
| LLM-interpreted executive report | `reporting.md` | Skill-orchestrated capture + interpretation handoff + deterministic render |
| Capture vetted preset evidence | `reporting.md` (+ `summary-tables.md`) | `bot_insights_capture.py` presets only |

## Data Firewall

Predefined report types (`executive_posture`, `control_review`, `soc_triage`,
`scorecard_brief`, `crawler_governance`, `edge_ops_impact`) run through a
deterministic capture path. When local Hydrolix credentials are configured,
that path queries the cluster directly and writes only the JSON result to
disk. The LLM sees the post-aggregation artifacts plus a
`bot_report_evidence.v1` packet — never the raw response. That is the
firewall.

When credentials don't resolve, the capture script emits a
`bot_hydrolix_mcp_query_request.v1` handoff packet and exits with code `42`.
Only then does the LLM run `mcp__*__run_select_query` — with the packet's
exact `cluster` and `validated_sql`, saving the response to the path the
packet specifies, then resuming the capture or report script with
`--raw-input`.

**Decision rule before any `run_select_query`:**

1. Is this a predefined report type? If no, MCP is fine.
2. Does `~/.config/hydrolix/clusters/<cluster>.env` (or `HYDROLIX_HOST` +
   credentials) resolve with no unresolved `op://` references? If yes, MCP
   is **forbidden** for this report's data — run the capture script.
3. Otherwise, run the capture script first. Only call `run_select_query`
   if it emits the handoff packet and exits `42`, and only with the
   packet's exact `cluster` and `validated_sql`.

**Scripts that may query Hydrolix:** `bot_insights_capture.py` and
`bot_insights_report.py` (which delegates to it). All other scripts consume
saved JSON only.

Exploratory, non-preset investigation SQL is unaffected — use Hydrolix MCP /
host query tools as today.

## LLM-Interpreted Report Flow

For `executive_posture`, `control_review`, `soc_triage`, `scorecard_brief`,
`crawler_governance`, and `edge_ops_impact` with executive/analyst prose:

1. Confirm scope (cluster, database, report type, window, baseline, output
   path). For `scorecard_brief`, default to single-entity render; pass
   `--fleet` for the multi-host view (mutually exclusive with
   `--entity-value`).
2. Run `scripts/bot_insights_report.py --mode evidence` first to produce the
   `bot_report_evidence.v1` packet. Do not query Hydrolix MCP before the
   script emits a `bot_hydrolix_mcp_query_request.v1` packet and exits `42`.
3. If the script emits that packet, run only the requested
   `run_select_query` with the packet's `cluster` and `validated_sql`, save
   the JSON to `target_raw_output_path`, and resume with `--raw-input`.
4. Hand the evidence packet to the LLM with its `interpretation_contract`.
   Require concise prose only: no new metrics, no root-cause claims, no
   malicious-traffic claims without additional artifacts. Use the
   human-readable `*_label` fields ("Cache miss rate high", "Origin impact",
   "Request host"), not snake_case identifiers. Do not name internal tables
   in prose — refer to "this report's evidence".
5. Build a `bot_report_input.v1` wrapper with the deterministic artifacts
   plus a single `analyst_notes` entry (`author_type: "llm"`,
   `show_data_sources: false` when citations would duplicate shown evidence).
6. Render via `scripts/render_report.py`. The renderer owns the template,
   tables, charts, timelines, and evidence limits — the LLM does not emit
   final HTML or Markdown layout.
7. Return final report path plus raw artifact and evidence packet paths.
   State whether MCP was used and cite the handoff packet if so.

## Triage Flow

1. Identify persona/decision: SOC, SEO, Edge/Ops, or executive.
2. Preserve requested time window, host, path, ASN, owner, crawler, or
   mitigation time.
3. Start with the summary whose retained dimensions fit the question.
4. Granularity defaults: day → QoQ/MoM/YoY; hour → weekday/hour seasonality;
   minute → short policy-change detail.
5. Attribute movement to concrete movers (ASN, path, host, owner, crawler,
   class, country, CDN, status). Use `compare_posture.py` for simple movers;
   `attribution.py` only when the user needs the advanced
   `bot_attribution_report.v1`.
6. Build evidence with at least two supporting dimensions before recommending
   action.
7. If a required dimension is not retained, apply the
   deployment-availability rule.

## Script Inventory

| Script | Purpose | Queries Hydrolix? |
|---|---|---|
| `bot_insights_capture.py` | Preset / guarded summary SQL capture | **Yes** (direct or MCP handoff via Data Firewall) |
| `bot_insights_report.py` | Orchestrates evidence + report + render | **Yes**, delegates to capture |
| `compare_delta.py` | Current-vs-baseline metric deltas from JSON | No |
| `compare_posture.py` | `bot_posture_movement.v1`, simple movers, control review | No |
| `attribution.py` | Advanced `bot_attribution_report.v1` aggregate-delta | No |
| `scorecard.py` | `bot_entity_scorecard.v1` + `bot_scorecard_index.v1` | No |
| `cache_origin_impact.py` | `cache_origin_impact_report.v1` from path-grain JSON | No |
| `render_report.py` | Render saved artifacts to Markdown / HTML | No |
| `baselines.py` | Shared deterministic baseline semantics (library) | No |

All artifact-only scripts accept MCP query results, saved JSON, or pasted
JSON. They must not contain database clients, connection config, or
credentials.

## Query Guardrails (skill-level)

Detailed footguns live in [references/pitfalls.md](references/pitfalls.md).
At the skill level:

- Always filter on the table's time column (`timestamp` alias when
  available, otherwise the physical source field such as `reqTimeSec`).
- Prefer summary tables when retained dimensions fit. Benchmark daily
  summaries first for QoQ — do not assume coarser summaries are needed.
- Be explicit about `hdx_cdn` when comparing Akamai SIEM, Akamai DS2, and
  other CDN sources.
- Treat Akamai-provided and Hydrolix-derived bot fields as independent
  signals. Divergence is evidence to investigate, not an error.
- Standard delta formula: `(current - baseline) / greatest(baseline, 1) * 100`.
  Reuse `scripts/baselines.py` for delta math, direction labels, granularity
  checks, JSON-safe values, and confidence labels — do not copy this logic.

## Common Mistakes

**Data firewall and deployment violations**

| Excuse / mistake | Reality |
|---|---|
| "I'll query `bot_detection` directly — it's in the schema docs" | Not deployed on observed clusters. Apply the deployment-availability rule and state the limitation in the artifact. |
| "MCP is faster than running the capture script" | If a cluster `.env` resolves, MCP is **forbidden** for predefined report data. Run capture first. |
| "I'll run `run_select_query` with my own SQL during a report flow" | Forbidden. Only run the exact `validated_sql` from a `bot_hydrolix_mcp_query_request.v1` packet, against its exact `cluster`. |
| "I'll name the internal table in the executive prose" | Refer to "this report's evidence" or by report type. Do not surface `bi_summary_*`, `bi_siem_policy_summary_*`, or `bot_agg_*` to the reader. |

**Query and prose pitfalls**

| Excuse / mistake | Reality |
|---|---|
| "I'll have the LLM write the final HTML — it's just templating" | `render_report.py` owns the template, tables, charts, and evidence limits. The LLM emits prose only, into `analyst_notes`. |
| "I'll add a DB client to `scorecard.py` so it can query directly" | Artifact scripts must not contain database clients, connection config, or credential handling. Only `bot_insights_capture.py` (and the report orchestrator that delegates to it) may reach Hydrolix. |
| "I'll cast `statusCode` to a string for consistency with request-level code" | TrafficPeak summary `statusCode` (and SIEM `status`) are numeric. Use numeric comparisons or cast with `toUInt32OrZero()`. |
| "Top-N volume is enough to call traffic malicious" | Stay evidence-first: deltas, source attribution, behavior, and impact. Never classify from a single signal. |

This skill is hardened against documented failure modes. See
[scenarios/](scenarios/) for the pressure-test corpus and the procedure
for re-running it after meaningful changes — and append a new scenario
when a failure is found in the wild.

## Red Flags — Stop and Re-check

- About to run `run_select_query` without first running the capture script
  for a predefined report type.
- About to substitute a non-deployed table because a needed dimension is
  absent from summaries.
- About to draft a recommendation, reference, or example that names a
  non-deployed table or column without invoking the deployment-availability
  rule in the same paragraph.
- About to write final HTML/Markdown layout from the LLM rather than feed
  prose into `analyst_notes`.
- About to hand the LLM a raw capture response instead of a
  `bot_report_evidence.v1` packet.
- About to import or `from` a database client (e.g. `clickhouse_connect`,
  `httpx`-driven Hydrolix calls) inside any script other than
  `bot_insights_capture.py` or the report orchestrator that delegates to it.
- About to claim a report "works" against a window without running a
  smoke-count query for the table and window first (e.g.
  `SELECT count() FROM <table> WHERE <time predicate>` returning > 0).
- About to write a recommendation whose evidence trail terminates at one
  dimension or one metric. Each recommendation should cite at least two
  supporting projections from the captured artifact.
