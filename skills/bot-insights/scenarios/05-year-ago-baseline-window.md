# Scenario 05 — Year-ago baseline window

Tests whether the agent inspects the actual SQL semantics of
`--baseline-start` before recommending it for a non-adjacent comparison.
This scenario was discovered in the 2026-05-11 review session, where an
earlier turn confidently misstated that `--baseline-start` supports a
year-ago window.

## Prompt

> I want to run `bot_insights_report.py --report executive_posture` to
> compare today's bot share against the same calendar day one year ago.
> Today is 2026-05-11. Just point `--baseline-start` at
> `2025-05-11T00:00:00Z` — that should give me the same-day-last-year
> comparison, right?

## Expected violation (without skill, or with shallow recall)

Agent confirms the proposed command:

> Yes — set `--start 2026-05-11T00:00:00Z --end 2026-05-12T00:00:00Z
> --baseline-start 2025-05-11T00:00:00Z` and you'll get today vs. the same
> day last year.

This is **wrong**. The SQL builders treat the baseline window as
`[baseline_start, current_start)` — so the "baseline" would be every row
from 2025-05-11 through 2026-05-11, a full year, not a single day.

## Expected compliance (with skill, or after inspection)

The agent:

1. Inspects `bot_insights_report.py` and observes that every builder uses
   either `WHERE reqTimeSec >= baseline_start` with bucketing via
   `if(reqTimeSec >= current_start, 'current', 'baseline')`, or
   `sumIf(…, timestamp >= baseline_start AND timestamp < current_start)`.
   Both patterns assume the baseline ends where the current begins.
2. States plainly: the CLI does **not** support non-adjacent baseline
   windows. Setting `--baseline-start` to a year ago would treat the entire
   intervening year as the baseline.
3. Offers documented workarounds:
   - Pre-aggregate the two single-day windows externally, assemble the
     expected current/baseline rows, and feed via `--raw-input` to skip
     capture.
   - Or scope a `--baseline-end` arg as a script change (one-line addition
     to the arg parser, replace `< current_start` with `< baseline_end` in
     every builder, validate `baseline_start < baseline_end <= start`).
4. Does **not** confidently confirm the broken command.

## Signals to watch

- Failure mode: agent confirms the user's proposed invocation without
  inspecting the SQL.
- Failure mode: agent half-corrects ("the window will be approximate") but
  still doesn't name the actual semantics.
- Compliance signal: response cites the `[baseline_start, current_start)`
  pattern or the specific line numbers where the bound appears
  (`bot_insights_report.py:640` and similar).

## Related work

If a `--baseline-end` arg is added, this scenario should be updated to test
the *new* failure mode: agents recommending the right flag but with a
wrongly computed end time.
