# bot-insights — TrafficPeak Akamai Summary Shape

Use this reference whenever the user is targeting a TrafficPeak Akamai
deployment or needs the concrete TrafficPeak summary table shape. This page is
limited to table routing, query conventions, and script-input normalization.

## Contents

- [Live Table Routing](#live-table-routing)
- [Posture Summary Shape](#posture-summary-shape)
- [SIEM Policy Summary Shape](#siem-policy-summary-shape)
- [Query Conventions](#query-conventions)
- [Normalizing Script Input](#normalizing-script-input)

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
and keep those queries explicitly time-bounded. Treat request-level surfaces
(`bot_detection`, `bot_detection_siem`) as not currently deployed unless you
have verified their presence on the target cluster — when absent, apply the
deployment-availability rule (SKILL.md).

Most panels use a constant `${timestamp}` variable with value `reqTimeSec`.
When translating dashboard SQL into standalone SQL, replace `${timestamp}` with
`reqTimeSec` for posture summaries and keep `timestamp` for SIEM policy
summaries.

## Posture Summary Shape

`akamai.bi_summary_hour` exposes source-style dimensions:

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

`akamai.bi_siem_policy_summary_hour` exposes these dimensions:

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

SQL uses the camelCase field names above. Normalize output to snake_case only
after querying, when preparing deterministic script input.

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

## Query Conventions

- Time filter: `reqTimeSec` for posture summaries, `timestamp` for SIEM policy
  summaries.
- Current-versus-baseline comparisons shift the equal-duration comparison
  window forward with `reqTimeSec + INTERVAL window_seconds SECOND AS time`.
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
- SIEM groupings commonly include `host`, `asn`, `policyId`, `actionClass`,
  and `botType`.

## Normalizing Script Input

The deterministic scripts expect stable public JSON names. After querying
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
