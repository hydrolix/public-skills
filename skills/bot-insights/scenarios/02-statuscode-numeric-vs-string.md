# Scenario 02 — statusCode numeric vs. string

Tests whether the agent adapts a request-level string-comparison pattern to
the deployed summary tables, where `statusCode` is numeric.

## Prompt

> Show me 4xx and 5xx error rates by hour from `bi_summary_hour` for the past
> week. I have a snippet from our request-level query code — just adapt it to
> the summary table:
>
> ```sql
> SELECT
>   toStartOfHour(timestamp) AS hour,
>   countIf(response_status_code >= '400' AND response_status_code < '500') AS errors_4xx,
>   countIf(response_status_code >= '500') AS errors_5xx
> FROM bot_detection
> WHERE timestamp >= now() - INTERVAL 7 DAY
> GROUP BY hour
> ```

## Expected violation (without skill)

Two failure modes:

1. **String comparison carried over.** Agent renames the table to
   `bi_summary_hour` and the column to `statusCode` but keeps `>= '400'` /
   `>= '500'` string comparisons. The query may run but ClickHouse string
   ordering of numerics is wrong on partial digits (`'5' > '4'` works, but
   `'500' > '4000'` is true).
2. **Field-name carryover.** Agent keeps `response_status_code` instead of
   the deployed-grain name `statusCode`.

## Expected compliance (with skill)

The agent:

1. Recognizes that `bot_summary_hour.statusCode` is **numeric** (per
   `references/pitfalls.md`: "TrafficPeak status fields are numeric").
2. Rewrites the predicates as `statusCode >= 400 AND statusCode < 500` and
   `statusCode >= 500`, or wraps with `toUInt32OrZero(statusCode)` if the
   schema check is uncertain.
3. Also notes the table+column rename (`bot_detection` → `bi_summary_hour`,
   `response_status_code` → `statusCode`) and the time column shift
   (`timestamp` → `reqTimeSec` on posture summaries).
4. Replaces `countIf` over raw rows with `sumIf(cnt_all, …)` or the
   appropriate merge function for aggregate-state columns.

## Signals to watch

- Failure mode: quoted numeric literals (`'400'`, `'500'`) survive into the
  rewritten query.
- Failure mode: agent doesn't notice that `bot_detection` is a non-deployed
  table (cross-loads Scenario 01).
- Compliance signal: response names "numeric" explicitly and/or cites
  `pitfalls.md`.
