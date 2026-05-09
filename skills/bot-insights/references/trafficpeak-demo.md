# bot-insights — TrafficPeak Demo

Use this reference when the user asks about the live `demo.trafficpeak.live`
Akamai project or the Grafana with-SIEM dashboards under
`dashboards.trafficpeak.live/dashboards/f/with-siem`.

This page was checked on 2026-05-08 against the live `akamai` project on
`demo.trafficpeak.live` and the live Grafana folder
`dashboards.trafficpeak.live/dashboards/f/with-siem/?orgId=116`.

Grafana access is configured in
`~/.config/hydrolix/grafana/dashboards.trafficpeak.live.env`. Use `op run` with
that env file to resolve the 1Password-backed `GRAFANA_TOKEN`; do not print or
copy the token into notes, scripts, or examples.

## Contents

- [Dashboard Set](#dashboard-set)
- [Live Table Routing](#live-table-routing)
- [Posture Summary Shape](#posture-summary-shape)
- [SIEM Policy Summary Shape](#siem-policy-summary-shape)
- [Dashboard Query Conventions](#dashboard-query-conventions)
- [Grafana API Checks](#grafana-api-checks)
- [Normalizing Script Input](#normalizing-script-input)

## Dashboard Set

The with-SIEM folder is represented by these dashboard views:

- Bot Insights - Overview and Alignment:
  `8854ea18a998d9e474f95713-with-siem-copy`
- Bot Insights - Multi-Domain Triage:
  `hdx-bot-insights-triage-with-siem-copy`
- Bot Insights - SOC What Changed:
  `a2c0be784d2b2220f7d2f121-with-siem-copy`
- Bot Insights - What Changed + Investigation:
  `289c9eb636bc56f5fffeab9d-with-siem-copy`
- Bot Insights - SEO Governance:
  `hdx-bot-insights-seo-gov-with-siem-copy`
- Bot Insights - Edge/Ops:
  `hdx-bot-insights-edge-ops-with-siem-copy`

All six live dashboards are tagged `akamai`, `bot-insights-cdn-1.1.9`, and
`with-siem`.

Common panel themes:

- posture scan: automation share, traffic composition, collection count, data
  lag
- what changed: request volume, 429 rate, 5xx rate, cache miss rate, origin
  TaT/TTFB, top ASN/path/category movers, newly seen entities
- multi-domain triage: bot-like share, crawler file status, AI-labeled share,
  reliability, origin latency, cache efficiency, AI source/ASN routing
- SEO governance: crawler file success, good-bot health, AI source
  composition, AI targeting paths, good-bot 429 watch
- Edge/Ops: cache efficiency, origin latency, endpoint origin cost,
  query-string churn, cache thrash, ASN concentration risk
- SIEM enrichment: threats blocked, auth failures, SIEM evidence by host or
  ASN/host, blocked/error mix by policy, SIEM policy deltas, auth failures by
  policy, and AI crawler SIEM enrichment

## Live Table Routing

For the `akamai` project:

- posture table variables should resolve by duration to
  `akamai.bi_summary_minute`, `akamai.bi_summary_hour`, or
  `akamai.bi_summary_day`
- SIEM policy table variables should resolve by duration to
  `akamai.bi_siem_policy_summary_minute`,
  `akamai.bi_siem_policy_summary_hour`, or
  `akamai.bi_siem_policy_summary_day`

Standard report captures use one summary-table selection rule for both posture
and SIEM policy surfaces:

- less than 3 hours: minute summary
- less than 48 hours: hour summary
- 48 hours or longer: day summary

Do not use request-level raw tables for standard report captures. Use raw tables
only for an ad hoc investigation that requires fields absent from summaries,
and keep those queries explicitly time-bounded.

Most live panels use a constant `${timestamp}` variable with value
`reqTimeSec`. When translating dashboard SQL into standalone SQL, replace
`${timestamp}` with `reqTimeSec` for posture summaries and keep `timestamp` for
SIEM policy summaries.

## Posture Summary Shape

Live `akamai.bi_summary_hour` exposes source-style dimensions:

- `reqTimeSec`
- `reqHost`
- `asn`
- `userAgentCategory`
- `isBotTraffic`
- `aiCategory`
- `resourceCategory`
- `reqMethod`
- `cacheStatus`
- `statusCode`
- `requestPathPattern`
- `country`
- `aiSource`
- `trafficCohort`

Important metrics and aggregate states:

- total requests: ``countMerge(`count()`)`` or per-row alias `cnt_all`
- bytes: ``sumMerge(`sum(totalBytes)`)`` or per-row alias `sum_totalBytes`
- origin TaT average:
  `sum_originTurnAroundTime_ms / nullIf(cnt_originTurnAroundTime, 0)`
- TTFB average: `sum_timeToFirstByte_ms / nullIf(cnt_timeToFirstByte, 0)`
- query-string pressure: `cnt_queryStringPresent` and
  `cnt_distinctQueryStrings`

When grouping across more than one stored summary row, use aggregate-state
merge functions from metadata instead of summing summary aliases.

Example posture query:

```sql
SELECT
  trafficCohort,
  aiCategory,
  userAgentCategory,
  countMerge(`count()`) AS requests,
  countMergeIf(`count()`, cacheStatus = false) AS cache_misses,
  round(
    sumIfMerge(`sumIf(Origin_TurnAroundTime, and(isNotNull(Origin_TurnAroundTime), greaterOrEquals(Origin_TurnAroundTime, 0)))`)
    / nullIf(countIfMerge(`countIf(and(isNotNull(Origin_TurnAroundTime), greaterOrEquals(Origin_TurnAroundTime, 0)))`), 0),
    2
  ) AS avg_origin_tat_ms
FROM akamai.bi_summary_hour
WHERE reqTimeSec >= now() - INTERVAL 7 DAY
GROUP BY trafficCohort, aiCategory, userAgentCategory
ORDER BY requests DESC
LIMIT 20
```

## SIEM Policy Summary Shape

Live `akamai.bi_siem_policy_summary_hour` exposes these dimensions:

- `timestamp`
- `host`, plus alias `reqHost`
- `asn`
- `userAgentCategory`
- `isBotTraffic`
- `aiCategory`
- `resourceCategory`
- `method`, plus alias `reqMethod`
- `status`, plus alias `statusCode`
- `country`
- `aiSource`
- `policyId`
- `actionClass`
- `botType`

Important metric aliases:

- `cnt_all`
- `cnt_blocked`
- `cnt_authFail`
- `avg_botScore`
- `uniq_clientIp`

Dashboard SQL uses the camelCase field names above. Normalize output to
snake_case only after querying, when preparing deterministic script input.

Example SIEM policy query:

```sql
SELECT
  policyId,
  actionClass,
  botType,
  countMerge(`count()`) AS requests,
  countIfMerge(`countIf(equals(actionClass, 'deny'))`) AS blocked,
  countIfMerge(`countIf(equals(authOutcome, 'fail'))`) AS auth_fail,
  avgIfMerge(`avgIf(botScore, greater(botScore, 0))`) AS avg_bot_score,
  uniqMerge(`uniq(clientIP)`) AS uniq_client_ip
FROM akamai.bi_siem_policy_summary_hour
WHERE timestamp >= now() - INTERVAL 30 DAY
GROUP BY policyId, actionClass, botType
ORDER BY requests DESC
LIMIT 50
```

## Dashboard Query Conventions

- Time filter: `reqTimeSec` for posture summaries, `timestamp` for SIEM policy
  summaries. In live Grafana JSON, posture panels often reference `${timestamp}`
  and that variable currently resolves to `reqTimeSec`.
- Current-versus-baseline panels shift the comparison equal-duration window
  forward with `reqTimeSec + INTERVAL window_seconds SECOND AS time`.
- Standard delta formula remains
  `(current - baseline) / greatest(baseline, 1) * 100`.
- Bot-like traffic is usually `trafficCohort IN ('Bot', 'AI')`.
- AI traffic is usually `trafficCohort = 'AI'`; use `aiCategory` and
  `aiSource` for routing.
- Good-bot health is usually represented by `userAgentCategory` cohorts such as
  `Search Engine Crawler` and crawler-file `resourceCategory` values such as
  `robots.txt` or `sitemap.xml`.
- Cache efficiency uses `cacheStatus = true`; cache miss pressure uses
  `cacheStatus = false`.
- Reliability panels use numeric `statusCode` comparisons on posture summaries
  and numeric `status`/`statusCode` comparisons on SIEM policy summaries.
- Endpoint panels use `requestPathPattern`, not raw `request_path`.
- Live SIEM panels use these groupings:
  - `SIEM Evidence by Host`: `host`
  - `SIEM Evidence by ASN / Host`: `asn`, `host`, `policyId`
  - `Blocked/Error Mix by Policy`: `policyId`, `actionClass`, `botType`
  - `SIEM Policy Deltas`: `policyId`, `actionClass`, `botType`
  - `Auth Failures by Policy`: `policyId`, `botType`, `asn`, `host`
  - `AI Crawler SIEM Enrichment`: `botType`, `actionClass`

## Grafana API Checks

Use the configured env file with `op run`:

```sh
op run --env-file ~/.config/hydrolix/grafana/dashboards.trafficpeak.live.env -- \
  sh -c 'curl -fsS -H "Authorization: Bearer $GRAFANA_TOKEN" "$GRAFANA_URL/api/folders/with-siem"'
```

Search the folder:

```sh
op run --env-file ~/.config/hydrolix/grafana/dashboards.trafficpeak.live.env -- \
  sh -c 'curl -fsS -G -H "Authorization: Bearer $GRAFANA_TOKEN" \
    --data-urlencode "folderUIDs=with-siem" \
    --data-urlencode "type=dash-db" \
    --data-urlencode "orgId=116" \
    "$GRAFANA_URL/api/search"'
```

## Normalizing Script Input

The deterministic scripts expect stable public JSON names. After querying live
TrafficPeak tables, normalize output before passing rows to scripts:

- `reqHost` or `host` -> `request_host`
- `asn` -> `client_asn`
- `userAgentCategory` -> `user_agent_category`
- `isBotTraffic` -> `is_bot_traffic`
- `aiCategory` -> `ai_category`
- `aiSource` -> `ai_source`
- `trafficCohort` -> `traffic_cohort`
- `resourceCategory` -> `resource_category`
- `reqMethod` or `method` -> `request_method`
- `cacheStatus` -> `cache_was_cached`
- `statusCode` or `status` -> `response_status_code`
- `requestPathPattern` -> `request_path_pattern`
- `policyId` -> `policy_id`
- `actionClass` -> `action_class`
- `botType` -> `bot_type`
- `cnt_authFail` -> `siem_auth_fail_requests`
- `avg_botScore` -> `avg_bot_score`
- `uniq_clientIp` -> `uniq_client_ip`

Keep the query table name in artifact metadata, for example
`akamai.bi_summary_hour` or `akamai.bi_siem_policy_summary_hour`, so later
reviewers can distinguish TrafficPeak summary evidence from canonical
request-level evidence.
