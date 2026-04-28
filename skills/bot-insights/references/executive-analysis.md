# bot-insights — Executive Analysis Patterns

Executive analysis should emphasize posture movement, health, and team routing.
Use summary tables first, especially daily summaries for quarter-over-quarter,
month-over-month, week-over-week, and year-over-year comparisons.
In SQL templates, replace `<posture_summary_day>` with `bi_summary_day` or an
equivalent metadata-confirmed `bot_summary_day`, and replace
`<siem_summary_day>` with `bi_siem_summary_day` or an equivalent
metadata-confirmed `bot_siem_summary_day`.

## Posture Movement [Director+]

```sql
-- Example: month-over-month posture by host from the daily summary.
SELECT
  period,
  request_host,
  sum(cnt_all) AS requests,
  round(sumIf(cnt_all, is_bot_traffic = true) / greatest(sum(cnt_all), 1) * 100, 2) AS bot_share_pct,
  round(sumIf(cnt_all, bot_class = 'bad') / greatest(sum(cnt_all), 1) * 100, 2) AS bad_bot_share_pct,
  round(sumIf(cnt_all, ai_category != '') / greatest(sum(cnt_all), 1) * 100, 2) AS ai_crawler_share_pct,
  round(sum(cnt_429) / greatest(sum(cnt_all), 1) * 100, 2) AS rate_429_pct,
  round(sum(cnt_cache_miss) / greatest(sum(cnt_all), 1) * 100, 2) AS cache_miss_pct
FROM (
  SELECT 'current' AS period, *
  FROM <project>.<posture_summary_day>
  WHERE timestamp >= toDateTime('<current_start>')
    AND timestamp < toDateTime('<current_end>')
  UNION ALL
  SELECT 'baseline' AS period, *
  FROM <project>.<posture_summary_day>
  WHERE timestamp >= toDateTime('<baseline_start>')
    AND timestamp < toDateTime('<baseline_end>')
)
GROUP BY period, request_host
ORDER BY request_host, period
```

Feed the aggregate rows into `scripts/compare_posture.py` to produce
`bot_posture_movement.v1` output.

## Multi-Domain Triage [Director+]

For environments with multiple sites, compare posture across domains to route
investigation to the right team.

```sql
SELECT
  request_host,
  sum(cnt_all) AS requests,
  round(sumIf(cnt_all, is_bot_traffic = true) / greatest(sum(cnt_all), 1) * 100, 2) AS bot_share_pct,
  round(sum(cnt_429) / greatest(sum(cnt_all), 1) * 100, 2) AS rate_429_pct,
  round(sum(cnt_5xx) / greatest(sum(cnt_all), 1) * 100, 2) AS rate_5xx_pct,
  round(sum(cnt_cache_miss) / greatest(sum(cnt_all), 1) * 100, 2) AS cache_miss_pct
FROM <project>.<posture_summary_day>
WHERE timestamp >= toDateTime('<start>')
  AND timestamp < toDateTime('<end>')
GROUP BY request_host
ORDER BY requests DESC
```

## Control Review [Director+, SOC]

After deploying a policy or control change, review target effects and collateral
movement. Keep this framed as control effectiveness unless external change
evidence supports stronger causal claims.

```sql
SELECT
  period,
  sum(cnt_all) AS requests,
  sum(cnt_blocked) AS siem_blocked_requests,
  sum(cnt_auth_fail) AS siem_auth_fail_requests,
  round(sum(cnt_5xx) / greatest(sum(cnt_all), 1) * 100, 2) AS rate_5xx_pct,
  round(sum(cnt_cache_miss) / greatest(sum(cnt_all), 1) * 100, 2) AS cache_miss_pct
FROM (
  SELECT 'before' AS period, *
  FROM <project>.<siem_summary_day>
  WHERE timestamp >= toDateTime('<before_start>')
    AND timestamp < toDateTime('<change_time>')
    AND policy_id = '<policy_id>'
  UNION ALL
  SELECT 'after' AS period, *
  FROM <project>.<siem_summary_day>
  WHERE timestamp >= toDateTime('<change_time>')
    AND timestamp < toDateTime('<after_end>')
    AND policy_id = '<policy_id>'
)
GROUP BY period
ORDER BY period
```

Use `post_change_vs_expected` in `references/baseline-comparison.md` when the
user provides an expected value or an expected baseline window.
