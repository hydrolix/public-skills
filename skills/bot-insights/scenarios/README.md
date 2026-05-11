# Pressure Scenarios

Re-runnable baseline tests for the bot-insights skill. Each scenario is a
prompt designed to elicit a specific failure mode under pressure (authority,
time, sunk cost, "just give me X"). Re-run against a fresh agent whenever the
skill changes meaningfully.

## How to run

1. Open a fresh agent session that does not have the bot-insights skill loaded.
2. Paste the scenario's `Prompt` verbatim. Match the time/authority framing.
3. Observe the response. Compare against the file's `Expected violation` and
   `Expected compliance` notes.
4. Re-run with the bot-insights skill loaded. The response should now match
   `Expected compliance`.

A scenario passes when the with-skill response matches compliance and the
without-skill response matches the documented violation. If both responses
match compliance, the scenario no longer applies pressure — replace it with a
harder variant.

## Scenarios

| File | Discipline tested |
|---|---|
| `01-deployment-availability.md` | Deployment-availability rule under explicit user pressure to query a non-deployed table |
| `02-statuscode-numeric-vs-string.md` | Summary-table `statusCode` is numeric, not string — even when the user pastes string-comparison code |
| `03-entity-ranking-to-scorecard.md` | Entity-ranking-for-handoff routes to `bot_entity_scorecard.v1`, not free-form prose |
| `04-single-signal-classification.md` | No classification from a single signal, even with strong volume framing |
| `05-year-ago-baseline-window.md` | `--baseline-start` defines an *adjacent* baseline window; non-adjacent comparisons need a different mechanism |

## Failure log

When a scenario fails, append a note here with date, agent build, and the
verbatim rationalization. The REFACTOR phase of `creating-skills` uses these
to extend the Common Mistakes / Red Flags tables in `SKILL.md`.
