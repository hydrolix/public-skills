---
name: bot-insights
description: >
  Analyze extended Bot Insights data with Akamai SIEM-enriched bot intelligence,
  summary-first SQL, deterministic scorecards, baseline/control reviews,
  policy-collateral checks, cache-origin impact, TrafficPeak demo analysis, and
  Markdown/HTML report rendering. Use when investigating bot scoring,
  classification confidence, bot intent, verified bot ownership, attack data,
  crawler governance, bot-driven CDN impact, or saved Bot Insights artifacts.
license: Apache-2.0
metadata:
  version: 1.0.0
  author: Hydrolix
  bundle: bot-insights
---

# Bot Insights Analysis

Use this skill to investigate bot behavior in the Hydrolix `bot-insights`
bundle without loading every query pattern up front. Start here to decide what
kind of question the user is asking, then load only the reference that contains
the relevant summary table, SQL pattern, or caveat.

This skill is compatible with Claude-style and Codex-style skill loading: it
uses standard `SKILL.md` frontmatter, relative markdown references, and no
agent-specific tool assumptions.

## When

Use this skill when the user asks about:

- Bot traffic share, bot scoring, bot class, confidence, intent, or producer.
- Verified and unverified bot ownership, crawler health, or AI crawler activity.
- Suspicious crawler spoofing, residential bot traffic, attack evidence, or
  Akamai SIEM bot/security enrichment.
- Bot-driven cache misses, query-string churn, origin load, latency, bandwidth,
  or rate-limiting impact.
- Before/after checks for blocks, cache-key changes, rate limits, bot-control
  policies, or security policy changes.
- Protected-population collateral checks or displacement after a policy,
  mitigation, or routing change.
- Bot posture across domains, hosts, ASNs, paths, countries, or CDN sources.
- Entity prioritization, deterministic scorecards, ranked investigation
  packets, or requests to rank risky/suspicious bot-related entities.

Do not use this skill for generic CDN traffic analysis unless bot fields are
central to the question; use `cdn-insights` for general cache, origin, traffic,
or error analysis.

## Why

The bundle combines normalized CDN access logs with bot enrichment. It is useful
because it lets an analyst connect automation identity to operational impact:
who the bot appears to be, whether that claim is credible, what it is doing, and
whether it is hurting cache, origin, SEO, or security posture.

The analysis should stay evidence-first. Do not classify traffic as malicious
from one signal alone. Prefer deltas, source attribution, behavior, and impact
over raw top-N volume.

## What

Primary request-level tables:

- `bot_detection`: request-level records with normalized CDN fields and bot
  intelligence.
- `bot_detection_siem`: SIEM-focused request-level records used by Akamai SIEM
  summaries.

Summary families:

- `bi_summary_*`: TrafficPeak posture summaries by host, ASN, user-agent
  category, bot flag, AI category/source, resource category, method, cache
  status, status code, path pattern, country, and traffic cohort. For the
  `demo.trafficpeak.live` Akamai project, use fully qualified
  `akamai.bi_summary_minute`, `akamai.bi_summary_hour`, or
  `akamai.bi_summary_day`.
- `bot_agg_*`: focused hourly and selected daily/minute summaries for host,
  ASN, path, resource, traffic, and bot class drilldowns.
- `bi_siem_policy_summary_*`: TrafficPeak SIEM policy summaries by host, ASN,
  user-agent category, bot flag, AI category/source, resource category, method,
  status, country, policy, action class, and SIEM bot type. For the Akamai
  project, use fully qualified `akamai.bi_siem_policy_summary_minute`,
  `akamai.bi_siem_policy_summary_hour`, or
  `akamai.bi_siem_policy_summary_day`.

Canonical field groups:

- Time: `timestamp`
- Request: `request_host`, `request_path`, `request_method`,
  `request_query_string`
- Response/cache: `response_status_code`, `response_total_bytes`,
  `cache_was_cached`
- Client/CDN: `client_ip`, `client_asn`, `client_country_iso_code`, `edge_pop`,
  `hdx_cdn`
- Bot identity: `is_bot_traffic`, `bot_score`, `bot_category`, `bot_type`,
  `bot_class`, `bot_confidence`, `bot_intent`, `bot_verification_tier`,
  `verified_bot_owner`, `ai_category`
- Security evidence: `attack_data`, `asn_type`

TrafficPeak/Akamai summary field aliases:

- Posture summaries use source-style names: `reqTimeSec`, `reqHost`, `asn`,
  `userAgentCategory`, `isBotTraffic`, `aiCategory`, `aiSource`,
  `trafficCohort`, `resourceCategory`, `reqMethod`, `cacheStatus`,
  `statusCode`, `requestPathPattern`, and `country`.
- SIEM policy summaries use `timestamp`, `host`/`reqHost`, `asn`,
  `userAgentCategory`, `isBotTraffic`, `aiCategory`, `aiSource`,
  `resourceCategory`, `method`/`reqMethod`, `status`/`statusCode`,
  `policyId`, `actionClass`, and `botType`.
- SIEM policy summary aliases are camelCase: `cnt_authFail`, `avg_botScore`,
  and `uniq_clientIp`.

Inspect metadata before querying and normalize deterministic script input back
to the canonical names expected by the script.

## Progressive Disclosure

Do not read every reference at startup. Load the smallest relevant file:

Use this file as the routing layer, not the full manual. For execution, pick the
narrow reference below and load only that file.

- For table shape, sources, key fields, and personas, read
  [references/data-model.md](references/data-model.md).
- For summary inventory, retained dimensions, and summary-first table
  selection, read [references/summary-tables.md](references/summary-tables.md).
- For the live TrafficPeak demo cluster, Akamai project, and with-SIEM
  dashboards, read [references/trafficpeak-demo.md](references/trafficpeak-demo.md).
- For quarter-over-quarter, month-over-month, week-over-week, year-over-year,
  seasonal, previous-window, and control-review baselines, read
  [references/baseline-comparison.md](references/baseline-comparison.md).
- For policy collateral, protected-population side effects, or displacement
  after a control or policy change, read
  [references/policy-collateral-analysis.md](references/policy-collateral-analysis.md).
- For full column inventory, flags, suppressed fields, and source coverage, read
  [references/schema.md](references/schema.md).
- For SOC/security investigations, deltas, movers, spoofing, attack evidence,
  classification, and bad bot behavior, read
  [references/soc-analysis.md](references/soc-analysis.md).
- For SEO, good bot governance, verified crawlers, and AI crawlers, read
  [references/seo-analysis.md](references/seo-analysis.md).
- For structured cache-busting, query-string churn, cache-miss movement, or
  origin-impact detector output, read
  [references/cache-origin-impact.md](references/cache-origin-impact.md) first.
  For broader Edge/Ops cache, origin, and bandwidth query patterns, read
  [references/edge-ops-analysis.md](references/edge-ops-analysis.md).
- For deterministic entity scorecards that synthesize posture movement, mover
  attribution, SEO governance, Edge/Ops impact, and SIEM/security evidence into
  reusable investigation packets, read
  [references/scorecard-analysis.md](references/scorecard-analysis.md).
- For the advanced aggregate-delta attribution CLI, accepted public JSON row
  shapes and conservative confidence caps, read
  [references/advanced-attribution.md](references/advanced-attribution.md).
- For executive posture, multi-domain triage, and post-mitigation verification,
  read [references/executive-analysis.md](references/executive-analysis.md).
- For rendering saved Bot Insights artifacts into Markdown or self-contained
  HTML reports, read [references/reporting.md](references/reporting.md).
- For runnable report-rendering demos, use the complete
  `bot_report_input.v1` wrappers in [examples/](examples/). They cover
  executive posture, SOC triage, control review, and crawler governance.
- Before finalizing a query or conclusion, scan
  [references/pitfalls.md](references/pitfalls.md).

## Analysis Routing

| User intent | Load | Deterministic output or workflow |
|-------------|------|----------------------------------|
| Reproduce or explain the live TrafficPeak demo dashboards | `references/trafficpeak-demo.md`; add persona file as needed | Summary-first SQL against `akamai.bi_summary_*` and `akamai.bi_siem_policy_summary_*` |
| What changed over a baseline? | `references/baseline-comparison.md` | `bot_posture_movement.v1` via `scripts/compare_posture.py --schema posture` |
| Which entity drove movement? | `references/baseline-comparison.md`; use `references/advanced-attribution.md` only for advanced aggregate-delta reports | `bot_mover_attribution.v1` or `bot_attribution_report.v1` |
| Did a known mitigation or policy change work? | `references/baseline-comparison.md` | `bot_control_review.v1` |
| Did a policy change affect protected traffic or displace traffic? | `references/policy-collateral-analysis.md`; add `references/advanced-attribution.md` for displacement ranking or `references/scorecard-analysis.md` for entity scorecards | `collateral_checks`, `displacement_checks`, `policy_displacement` attribution, and `policy_collateral` scorecard features |
| Suspicious automation, SIEM, spoofing, attack evidence | `references/soc-analysis.md`; add `references/summary-tables.md` when choosing tables | Summary-backed SOC queries, SIEM enrichment, or scorecard-ready rows |
| Crawler availability, good bot health, AI crawler governance | `references/seo-analysis.md` | SEO/crawler governance query patterns and scorecard-ready rows |
| Cache busting, cache misses, origin pressure | `references/cache-origin-impact.md` for structured detector output; otherwise `references/edge-ops-analysis.md` | `cache_origin_impact_report.v1` or Edge/Ops query evidence |
| Executive posture, routing across teams, mitigation verification | `references/executive-analysis.md`; add `references/reporting.md` for final report rendering | Executive posture artifacts and rendered reports |
| Rank entities for handoff or repeated triage | `references/scorecard-analysis.md` | `bot_entity_scorecard.v1` and `bot_scorecard_index.v1` |
| Render saved artifacts | `references/reporting.md` | Markdown or self-contained HTML from `scripts/render_report.py` |
| Generate an LLM-interpreted executive posture report | `references/reporting.md` | Skill-orchestrated `executive_posture` evidence capture, LLM interpretation handoff, and deterministic final rendering |
| Capture vetted Bot Insights report evidence | `references/reporting.md`; add `references/summary-tables.md` when selecting presets | `scripts/bot_insights_capture.py` presets only; use Hydrolix MCP/query tools for broad investigation SQL |

## LLM-Interpreted Report Flow

Use this flow when the user wants a finished `executive_posture` or
`control_review` report with executive or analyst interpretation. Keep the
report data path deterministic; the LLM may only write the interpretation text
that is passed into the report template. The same flow applies to SOC triage,
crawler governance, scorecard brief, and edge & origin cost reports — all are
wired through `scripts/bot_insights_report.py`.

1. Confirm or infer the report scope: cluster, database, report type, current
   window, baseline behavior, output format, and output path. Ask the user only
   for missing scope that cannot be safely inferred.
2. Run the skill-owned deterministic script first, normally
   `scripts/bot_insights_report.py --mode evidence`, and save the
   `bot_report_evidence.v1` packet. Do not query Hydrolix MCP before the script
   emits a `bot_hydrolix_mcp_query_request.v1` packet and exits with code `42`.
3. If the script emits that packet, run only the requested Hydrolix MCP
   `run_select_query` with the packet's `cluster` and `validated_sql`, save the
   complete JSON response to `target_raw_output_path`, and resume the same
   evidence command with `--raw-input`. Do not run exploratory SQL as part of
   this report flow.
4. Hand the evidence packet to the LLM with the packet's
   `interpretation_contract`. Require concise interpretation prose only: no new
   metrics, no root-cause claims, no malicious-traffic claims without additional
   artifacts, and no Hydrolix queries from the interpretation step.
5. Build a `bot_report_input.v1` wrapper with the deterministic artifacts and a
   single `analyst_notes` entry for the returned prose. Use
   `author_type: "llm"`, title it `Executive Interpretation` or another
   user-appropriate label, and set `show_data_sources: false` when citations
   would duplicate charts and tables already shown as evidence.
6. Render the wrapper through `scripts/render_report.py`. The renderer owns the
   template, tables, charts, timelines, warnings, and evidence limits. Do not
   ask the LLM to emit final HTML or Markdown layout.
7. Return the final report path plus the raw artifact and evidence packet paths.
   State whether MCP was used, and if it was used, point to the handoff packet
   that authorized it.

## Data Firewall

Predefined Bot Insights report types — `executive_posture`, `control_review`,
`soc_triage`, `scorecard_brief`, `crawler_governance`, `edge_ops_impact` — run
through a deterministic capture path. When local Hydrolix credentials are
configured, this path runs the validated SQL directly against the cluster's
`/query/` endpoint and writes only the JSON result to disk. The LLM never sees
the raw response — it sees the post-aggregation deterministic artifacts the
producer scripts emit (`bot_posture_movement.v1`,
`bot_entity_scorecard.v1`, etc.) plus a `bot_report_evidence.v1` packet. That
is the firewall.

When credentials don't resolve, the same capture path emits a
`bot_hydrolix_mcp_query_request.v1` handoff packet and exits with code `42`.
Only then does the LLM run `mcp__*__run_select_query` — with the packet's exact
`cluster` and `validated_sql`, saving the response to the path the packet
specifies, then resuming the capture or report script with `--raw-input`.

Which scripts are in scope:

- Run queries (capture path): [scripts/bot_insights_capture.py](scripts/bot_insights_capture.py),
  and [scripts/bot_insights_report.py](scripts/bot_insights_report.py) which
  delegates to it.
- Never run queries: [scripts/scorecard.py](scripts/scorecard.py),
  [scripts/attribution.py](scripts/attribution.py),
  [scripts/cache_origin_impact.py](scripts/cache_origin_impact.py),
  [scripts/compare_posture.py](scripts/compare_posture.py),
  [scripts/compare_delta.py](scripts/compare_delta.py),
  [scripts/baselines.py](scripts/baselines.py), and
  [scripts/render_report.py](scripts/render_report.py). They consume saved
  JSON only.

Exploratory analysis — broad investigation SQL that doesn't fit a predefined
report — is unaffected. Use Hydrolix MCP / host query tools as today; you can't
write a deterministic capture for an open-ended investigation.

Decision rule before running any `run_select_query`:

1. Is this a predefined report type? If no, MCP is fine.
2. Is there a `~/.config/hydrolix/clusters/<cluster>.env` file (or
   `HYDROLIX_HOST`/`HDX_HOSTNAME` plus a token or user/password) where every
   value resolves and isn't an unresolved `op://` reference? If yes, MCP is
   forbidden for this report's data — run the capture script.
3. Otherwise, run the capture script first. Only call `run_select_query` if
   the script emits a `bot_hydrolix_mcp_query_request.v1` packet and exits
   `42`, and then only with the packet's exact `cluster` and `validated_sql`.

Reports for which the script-orchestrated capture path is wired:
`executive_posture`, `control_review`, `soc_triage`, `scorecard_brief`, and
`crawler_governance`, and `edge_ops_impact`.

## Triage Flow

1. Identify the persona and decision: SOC, SEO, Edge/Ops, or executive posture.
2. Preserve the requested time window, host/domain, path, ASN, owner, crawler, or
   mitigation time if supplied.
3. Start with the summary table whose retained dimensions fit the question.
4. For posture movement, prefer day summaries for QoQ/MoM/YoY, hour summaries
   for weekday/hour seasonality, and minute summaries for short policy-change
   detail.
5. Attribute the change to concrete movers: ASN, path, host, bot owner, crawler,
   bot class, country, CDN, or status code. Keep existing simple mover packets
   on [scripts/compare_posture.py](scripts/compare_posture.py); use
   [scripts/attribution.py](scripts/attribution.py) only when the user needs the
   advanced `bot_attribution_report.v1` aggregate-delta report.
6. Build evidence with at least two supporting dimensions before recommending
   action.
7. When the decision requires entity prioritization rather than another panel,
   produce scorecard-ready aggregate rows and run
   [scripts/scorecard.py](scripts/scorecard.py) to emit
   `bot_entity_scorecard.v1` packets plus a `bot_scorecard_index.v1`.
8. Use request-level tables only when a required dimension is not retained in
   summaries, and state the reason.

## Query Guardrails

- Always filter on the table's time column. Prefer the `timestamp` alias when
  metadata exposes it; otherwise use the physical source time field such as
  `reqTimeSec`.
- Prefer summary tables when retained dimensions fit. Do not assume QoQ queries
  need monthly or quarterly summaries; benchmark daily summaries first.
- Use numeric comparisons for TrafficPeak summary `statusCode`/SIEM `status`.
  Use string comparisons for request-level `response_status_code`, or cast
  explicitly with `toUInt32OrZero()` when numeric operations are needed.
- Prefer normalized fields over suppressed raw variants.
- Support both canonical Bot Insights names and source-style Akamai names. Common
  aliases include `timestamp`/`reqTimeSec`, `request_host`/`reqHost`,
  `client_asn`/`asn`, `client_country_iso_code`/`country`,
  `client_city`/`city`, `response_status_code`/`statusCode`,
  `response_total_bytes`/`totalBytes`, and
  `cache_was_cached`/`cacheStatus`.
- On `akamai.bi_siem_policy_summary_*`, use metadata-confirmed SIEM names such
  as `policyId`, `actionClass`, `botType`, `cnt_authFail`, `avg_botScore`, and
  `uniq_clientIp`, or use the aggregate-state merge functions directly.
- Be explicit about `hdx_cdn` when comparing Akamai SIEM, Akamai DS2, and other
  CDN sources.
- Treat Akamai-provided bot fields and Hydrolix-derived bot fields as separate
  signals. Divergence is evidence to investigate, not an automatic error.
- For before/after checks, use the same baseline formula as the references:
  `(current - baseline) / greatest(baseline, 1) * 100`.
- Reuse [scripts/baselines.py](scripts/baselines.py) for shared deterministic
  baseline semantics in Bot Insights scripts: numeric parsing, delta math,
  direction labels, count support, granularity checks, JSON-safe values, and
  confidence labels. Do not copy this logic into new scripts.
- Optionally use [scripts/compare_delta.py](scripts/compare_delta.py) to compute
  that formula from pasted current/baseline metric JSON. Use it only for numeric
  deltas; do not use it to classify bot intent or recommend action.
- Use [scripts/compare_posture.py](scripts/compare_posture.py) for structured
  posture movement, simple mover attribution, and control-review JSON.
  It emits the existing `bot_posture_movement.v1`,
  `bot_mover_attribution.v1`, and `bot_control_review.v1` packet shapes. Keep
  simple posture and mover workflows here unless the user explicitly needs the
  advanced attribution report; the script accepts MCP query results, saved JSON,
  or pasted aggregate JSON only and does not query Hydrolix.
- Use [scripts/attribution.py](scripts/attribution.py) for advanced
  aggregate-delta attribution reports in `bot_attribution_report.v1`. The v1a
  standalone CLI accepts file, stdin, saved MCP result JSON, pasted JSON,
  wrapper objects, and list-of-dict aggregate rows; it does not query Hydrolix.
  Public JSON from this path is capped below high confidence, treats
  completeness and scorecard-safety fields as caller assertions, and exposes no
  scorecard export mode.
- Use [scripts/scorecard.py](scripts/scorecard.py) for deterministic
  scorecard artifacts after Hydrolix has produced entity-level aggregate rows.
  It accepts MCP query results, saved JSON, or pasted JSON only; it does not
  query Hydrolix. Missing feature inputs must remain `not_evaluated_features`,
  not implicit safe evidence.
- Use [scripts/cache_origin_impact.py](scripts/cache_origin_impact.py) for
  deterministic `cache_origin_impact_report.v1` artifacts after Hydrolix has
  produced path-grain aggregate rows. It accepts MCP query results, saved JSON,
  or pasted JSON only; it does not query Hydrolix, prove causality, or
  recommend mitigations.
- Use [scripts/render_report.py](scripts/render_report.py) to render saved Bot
  Insights artifacts into Markdown or self-contained HTML reports. It accepts
  existing artifact JSON only; it does not query Hydrolix, recompute scores, or
  infer missing evidence.
- Use [scripts/bot_insights_capture.py](scripts/bot_insights_capture.py) for
  vetted Bot Insights presets and guarded Bot Insights summary SQL. It is the
  one script that may reach Hydrolix; how it routes between direct `/query/`
  HTTP and an MCP handoff packet is governed by the [Data Firewall](#data-firewall).
  It is not a generic Hydrolix query runner.
- Use [scripts/bot_insights_report.py](scripts/bot_insights_report.py) for
  scripted `executive_posture`, `control_review`, `scorecard_brief`,
  `soc_triage`, `crawler_governance`, and `edge_ops_impact` report, evidence,
  and template requests. The script calls capture; if capture returns an MCP handoff
  packet, the report script prints that packet and exits with the documented
  `needs MCP` code. After the LLM/agent saves the MCP result JSON, rerun with
  `--raw-input <path>` to add report metadata, produce local artifacts, and
  emit rendered reports or `bot_report_evidence.v1` packets. The LLM may fill prose in a template from that packet only. `~/src/utils/bot-insights-report` remains a thin
  executable convenience wrapper around this skill script.
- Broad, non-preset, exploratory, or non-Bot-Insights SQL investigation belongs
  in the LLM workflow through Hydrolix MCP / host query tools, not in
  `bot_insights_capture.py` — see the Data Firewall section's exploratory
  carve-out.
- Artifact scripts must not contain database clients, connection configuration,
  or credential handling. The intentional exceptions are
  `bot_insights_capture.py` and the report orchestration path that calls it.

## Reference Map

- [references/data-model.md](references/data-model.md): bundle overview, key
  fields, and personas.
- [references/summary-tables.md](references/summary-tables.md): summary table
  inventory, retained dimensions, metrics, and request-level guidance.
- [references/trafficpeak-demo.md](references/trafficpeak-demo.md): live
  `demo.trafficpeak.live` Akamai project and with-SIEM dashboard conventions.
- [references/baseline-comparison.md](references/baseline-comparison.md):
  comparison methods, granularity selection, confidence reasons, output schemas,
  and SQL templates.
- [references/policy-collateral-analysis.md](references/policy-collateral-analysis.md):
  protected-population collateral checks, displacement checks, and scorecard
  inputs for policy-change safety reviews.
- [references/schema.md](references/schema.md): full schema with type, flags,
  and source coverage.
- [references/soc-analysis.md](references/soc-analysis.md): SOC and security
  query patterns.
- [references/seo-analysis.md](references/seo-analysis.md): crawler governance
  and AI crawler query patterns.
- [references/cache-origin-impact.md](references/cache-origin-impact.md):
  structured `cache_origin_impact_report.v1` scope, SQL template guidance,
  standalone input/output examples, and detector boundaries.
- [references/edge-ops-analysis.md](references/edge-ops-analysis.md): cache,
  origin, and bandwidth query patterns.
- [references/scorecard-analysis.md](references/scorecard-analysis.md):
  deterministic entity scorecards, summary-first aggregate templates, SIEM
  enrichment, and reusable investigation packets.
- [references/advanced-attribution.md](references/advanced-attribution.md):
  advanced aggregate-delta attribution CLI, accepted v1a public input shapes,
  and confidence caps.
- [references/executive-analysis.md](references/executive-analysis.md):
  posture, multi-domain triage, and mitigation verification.
- [references/reporting.md](references/reporting.md): renderer input grammar,
  supported report types, warning and evidence-limit expectations, and the
  artifact-only boundary.
- [references/pitfalls.md](references/pitfalls.md): known schema and analysis
  footguns.
- [scripts/compare_delta.py](scripts/compare_delta.py): compute current versus
  baseline absolute and percentage deltas from simple metric JSON.
- [scripts/compare_posture.py](scripts/compare_posture.py): emit structured
  Bot Insights posture movement, simple mover attribution, and control-review
  JSON from aggregate JSON.
- [scripts/attribution.py](scripts/attribution.py): emit conservative
  `bot_attribution_report.v1` aggregate-delta attribution reports from public
  aggregate JSON.
- [scripts/scorecard.py](scripts/scorecard.py): emit deterministic
  `bot_entity_scorecard.v1` and `bot_scorecard_index.v1` artifacts from
  entity-level aggregate JSON.
- [scripts/cache_origin_impact.py](scripts/cache_origin_impact.py): emit
  deterministic `cache_origin_impact_report.v1` artifacts from path-grain
  aggregate JSON.
- [scripts/render_report.py](scripts/render_report.py): render existing Bot
  Insights artifacts or `bot_report_input.v1` wrappers into Markdown or
  self-contained HTML reports.
- [scripts/bot_insights_capture.py](scripts/bot_insights_capture.py): capture
  vetted Bot Insights preset or guarded summary SQL results. It uses direct
  Hydrolix `/query/` HTTP only when local credentials are configured; otherwise
  it emits a Hydrolix MCP handoff packet.
- [scripts/bot_insights_report.py](scripts/bot_insights_report.py): orchestrate
  deterministic Bot Insights report, evidence, and template generation through
  capture, MCP handoff, `--raw-input` resume, and local artifacts.
- [examples/](examples/): complete report-rendering demo payloads.
