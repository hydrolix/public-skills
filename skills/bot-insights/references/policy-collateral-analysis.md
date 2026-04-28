# bot-insights - Policy Collateral Analysis

Use this reference when a Bot Insights request asks whether a bot-control,
security, rate-limit, cache-key, or policy change had unintended side effects
or shifted traffic into another segment.

Policy collateral analysis is an effectiveness and safety review. It does not
prove causality by itself. Use it after Hydrolix has produced aggregate
before/after or current/baseline rows, and pair it with external evidence that
the reviewed policy change happened at the stated time.

## Contents

- [Workflow](#workflow)
- [Control Review Inputs](#control-review-inputs)
- [Advanced Displacement Attribution](#advanced-displacement-attribution)
- [Scorecard Inputs](#scorecard-inputs)
- [Interpretation](#interpretation)

## Workflow

1. Identify the reviewed policy, control, cache-key change, or mitigation time.
2. Produce a target-effect control review with `scripts/compare_posture.py`.
3. Add collateral checks for protected populations such as good bots, verified
   crawlers, AI crawlers, governance surfaces, and business-critical paths.
4. Add displacement checks for related hosts, paths, ASNs, bot classes, CDN
   sources, SIEM policies, or action outcomes.
5. When displacement needs ranked follow-up, run
   `scripts/attribution.py --analysis policy_displacement` on retained
   dimension aggregates.
6. Use `scripts/scorecard.py` when the workflow needs ranked entities for
   follow-up.

## Control Review Inputs

`bot_control_review.v1` already carries two policy-collateral surfaces:

- `collateral_checks`: metrics that should remain stable or improve after the
  policy change, such as good bot 429s, crawler 5xx rate, governance-surface
  failures, cache miss rate, origin p95, or business-path errors.
- `displacement_checks`: related populations where unwanted traffic may have
  moved, such as another host, path, ASN, bot class, SIEM policy, CDN source, or
  action outcome.

Each check should include the metric, before/after or after/expected values,
status, confidence, and confidence reasons when available. Renderers preserve
these checks as evidence; they do not infer missing collateral or displacement
rows.

## Advanced Displacement Attribution

Use `bot_attribution_report.v1` from `scripts/attribution.py` when a policy
collateral review needs to rank where traffic moved after a known change:

```sh
uv run python skills/bot-insights/scripts/attribution.py \
  --file aggregate.json \
  --metric requests \
  --dimensions request_host,bot_class \
  --analysis policy_displacement
```

The aggregate rows should cover one metric and one retained dimension set, such
as `request_host`, `request_path_norm`, `client_asn`, `bot_class`,
`ai_category`, SIEM `policy_id`, or `action_taken`. The report preserves
`policy_change`, `policy_change_window`, `reviewed_policy`, and `target_effect`
metadata when provided.

Policy displacement mode adds a `displacement_summary` to the attribution
report with positive delta, negative delta, net delta, largest increase, and
largest decrease across returned rows. Treat these as movement evidence only;
they require external policy-change evidence and do not prove causality.

## Scorecard Inputs

`scripts/scorecard.py` recognizes these policy-collateral aggregate inputs:

- `good_bot_collateral_429_requests`,
  `collateral_good_bot_429_requests`, or
  `policy_collateral_good_bot_429_requests`; `good_bot_429_requests` is also
  accepted when the protected-population aggregate comes from a shared
  crawler/governance enrichment query.
- `policy_collateral_error_rate_pct`, `collateral_error_rate_pct`, or
  `good_bot_collateral_error_rate_pct`; `good_bot_error_rate_pct` is also
  accepted from shared protected-population aggregates.
- `current_displacement_requests` and `baseline_displacement_requests`
  (also accepted with the metric aliases `other_scope_requests` or
  `post_policy_displacement_requests`)

These fields score the `policy_collateral` domain. Missing inputs remain
`not_evaluated_features`; they are not interpreted as proof that a policy had
no collateral impact.

When no external policy-change context exists, use the protected-population
fields available from `bi_summary_*` and omit displacement fields. The
scorecard evaluates good-bot 429/error-rate inputs, including zero values, and
skips displacement rather than reporting it as missing. Displacement should be
added only when a caller supplies a policy/control change and a displacement
population to compare.

## Interpretation

Treat policy collateral findings as a review queue:

- Target effects say whether the intended metric moved.
- Collateral checks say whether protected populations or operational surfaces
  were affected.
- Displacement checks say whether traffic shifted to another retained segment.
- Scorecards rank affected entities for follow-up.

Do not declare a policy successful from the target metric alone when
collateral or displacement evidence is missing.
