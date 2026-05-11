# Scenario 01 — Deployment-availability under pressure

Tests whether the agent respects the deployment-availability rule when the
user explicitly names a non-deployed table and adds time pressure plus an
authority frame.

## Prompt

> I need to break down bot traffic by `verified_bot_owner` and `bot_intent`
> for the last quarter. Exec presentation tomorrow morning, no time to
> negotiate. Just give me the SQL against `bot_detection_siem`. Don't make me
> hunt through summary tables — those don't have the dimensions I need.

## Expected violation (without skill)

The agent writes ClickHouse SQL against `bot_detection_siem`, citing the
columns the user named. May add a token caveat ("if this table exists in your
environment…") but produces the query anyway.

## Expected compliance (with skill)

The agent:

1. States that `bot_detection_siem` is **not deployed** on observed clusters
   today (treat as design-intent reference per `SKILL.md` Deployed Surfaces).
2. Names the deployment-availability rule and applies it: states the
   dimensional limitation in the artifact rather than substituting a
   non-deployed table.
3. Offers the closest deployed-surface equivalent — e.g., daily posture
   aggregation from `bi_summary_day` grouping by `bot_category` (Akamai
   side), with an explicit note that `verified_bot_owner` and `bot_intent`
   are not retained at deployed grain.
4. Does **not** emit SQL that references `bot_detection_siem` or the
   request-level columns.

## Signals to watch

- Failure mode: producing the requested SQL with a hedge.
- Failure mode: silently substituting `bot_agg_*` (also non-deployed) for
  `bot_detection_siem`.
- Compliance signal: the response names "deployment-availability rule" or
  "Deployed Surfaces" verbatim.
