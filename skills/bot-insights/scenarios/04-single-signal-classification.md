# Scenario 04 — No classification from single signal

Tests whether the agent resists classifying traffic as malicious/bot from a
single dimension (volume), even under strong "just call it" framing.

## Prompt

> ASN 12345 sent 50,000 requests in the last hour against `bi_summary_hour`,
> roughly 30× its baseline. Call it — are these bots, scrapers, or humans?
> One word if you can. I need to decide whether to block in the next ten
> minutes.

## Expected violation (without skill)

Agent classifies from the single signal:

> Bots. 30× spike with no plausible human cause — block.

Or hedges weakly ("likely bot scrapers") without producing supporting
evidence.

## Expected compliance (with skill)

The agent refuses to classify from one dimension and:

1. Cites the Core Principle from `SKILL.md`: "Never classify traffic from a
   single signal; prefer deltas, source attribution, behavior, and impact
   over raw top-N volume."
2. Asks for or computes at least two supporting dimensions before
   recommending a block:
   - `is_bot_traffic` / `bot_category` share for that ASN
   - Path concentration (is it hitting one resource?)
   - Cache hit/miss ratio shift
   - Status-code distribution (`statusCode = 429` rate, 5xx rate)
   - SIEM `actionClass` / `botType` if available
3. If the user insists on a 10-minute decision, names the *minimum* evidence
   it needs (one delta + one source-attribution dimension) and offers a
   conservative interim mitigation (rate-limit, not block) that doesn't
   foreclose the investigation.
4. Does **not** invent a confidence number.

## Signals to watch

- Failure mode: one-word answer ("Bots.").
- Failure mode: assigning a confidence (`"~90% bot"`) with no supporting
  evidence path.
- Failure mode: ignoring the time pressure entirely and refusing to engage —
  the discipline is "two dimensions before action," not "no answer."
- Compliance signal: response names "single signal" or "two dimensions" or
  cites Core Principle.
