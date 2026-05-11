# Scenario 03 — Entity ranking routes to scorecard

Tests whether the agent recognizes an entity-ranking-for-handoff request and
produces a deterministic `bot_entity_scorecard.v1` packet instead of
free-form top-N prose.

## Prompt

> For an executive review I need the top 10 hosts driving bot-share movement
> quarter-over-quarter. Free-form prose is fine — just give me a ranked list
> with a sentence on each. No JSON, no scripts, keep it short.

## Expected violation (without skill)

Agent writes free-form prose:

> 1. example.com — bot share rose 12 percentage points QoQ, driven by a new
>    AI crawler …
> 2. api.example.com — …

Skips the scorecard handoff entirely. The exec can read it but downstream
consumers (deck builders, SIEM exports, follow-up triage) can't ingest it.

## Expected compliance (with skill)

The agent:

1. Recognizes "top N entities for executive review" as the
   entity-ranking-for-handoff pattern (per `SKILL.md` Analysis Routing,
   "Rank entities for handoff" → `scorecard-analysis.md`).
2. Produces (or instructs to produce) a `bot_entity_scorecard.v1` artifact
   by running `scripts/bot_insights_report.py --report scorecard_brief
   --mode evidence …` or `scripts/scorecard.py` on pre-captured aggregate
   rows.
3. Adds prose into the `analyst_notes` slot, **not** as the primary output.
4. Explains why: a scorecard is reproducible, machine-consumable, and feeds
   the deterministic renderer; free-form prose isn't.
5. If the user pushes back ("no scripts"), offers the smallest possible
   deviation: run the script, send back the rendered Markdown, paste prose
   into a separate field — still anchored on the scorecard artifact.

## Signals to watch

- Failure mode: ranked prose list with no mention of scorecard or evidence
  packet.
- Failure mode: invents a `score` field from raw volume without the
  scorecard's deterministic feature set (`rate_429_delta_high`,
  `cache_miss_delta_high`, etc.).
- Compliance signal: response names `bot_entity_scorecard.v1` or
  `scorecard_brief` or `scripts/scorecard.py`.
