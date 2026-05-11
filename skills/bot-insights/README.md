# Bot Insights Skill

This README is for humans browsing the repository on GitHub.

Agent runtime instructions live in [SKILL.md](SKILL.md). If this README ever
conflicts with `SKILL.md`, the skill file wins. Treat this page as an index,
orientation guide, and quick tour of the examples, not as an authoritative
source for agent behavior.

## What This Skill Covers

`bot-insights` helps agents investigate bot behavior in Hydrolix Bot Insights
data and produce evidence-backed artifacts for several audiences:

- executive posture and routing
- SOC triage, spoofing, and security evidence
- crawler governance and AI crawler health
- cache-busting, origin pressure, and Edge/Ops impact
- control-change reviews and protected-population collateral checks
- deterministic entity scorecards and report rendering

The skill is designed around an evidence-first rule: connect automation
identity to operational impact, avoid single-signal classification, and keep
movement, attribution, confidence, and missing evidence visible.

## Directory Map

| Path | Purpose |
| --- | --- |
| [SKILL.md](SKILL.md) | Authoritative agent entrypoint, routing rules, data firewall, and workflow constraints. |
| [references/](references/) | Focused reference files for data model, report types, query patterns, pitfalls, and analysis modes. |
| [scripts/](scripts/) | Deterministic capture, comparison, scorecard, attribution, and report-rendering tools. |
| [examples/](examples/) | Demo report inputs, rendered reports, and worked conversation examples. |
| [scenarios/](scenarios/) | Scenario notes for common reasoning and validation cases. |
| [agents/](agents/) | Agent packaging metadata. |

## Rendered Examples

The rendered examples are useful for reviewing report structure and language.
Use the HTML preview links to view the self-contained reports in a browser; use
the source links to inspect the checked-in Markdown.

| Report | Rendered HTML | Source Markdown |
| --- | --- | --- |
| Executive posture | [View HTML](https://htmlpreview.github.io/?https://github.com/hydrolix/hydrolix-ai-toolkit/blob/main/skills/bot-insights/examples/rendered/executive-posture.html) | [Markdown](examples/rendered/executive-posture.md) |
| Control review | [View HTML](https://htmlpreview.github.io/?https://github.com/hydrolix/hydrolix-ai-toolkit/blob/main/skills/bot-insights/examples/rendered/control-review.html) | [Markdown](examples/rendered/control-review.md) |
| SOC triage | [View HTML](https://htmlpreview.github.io/?https://github.com/hydrolix/hydrolix-ai-toolkit/blob/main/skills/bot-insights/examples/rendered/soc-triage.html) | [Markdown](examples/rendered/soc-triage.md) |
| Scorecard brief | [View HTML](https://htmlpreview.github.io/?https://github.com/hydrolix/hydrolix-ai-toolkit/blob/main/skills/bot-insights/examples/rendered/scorecard-brief.html) | [Markdown](examples/rendered/scorecard-brief.md) |
| Crawler governance | [View HTML](https://htmlpreview.github.io/?https://github.com/hydrolix/hydrolix-ai-toolkit/blob/main/skills/bot-insights/examples/rendered/crawler-governance.html) | [Markdown](examples/rendered/crawler-governance.md) |
| Edge/Ops impact | [View HTML](https://htmlpreview.github.io/?https://github.com/hydrolix/hydrolix-ai-toolkit/blob/main/skills/bot-insights/examples/rendered/edge-ops-impact.html) | [Markdown](examples/rendered/edge-ops-impact.md) |

## Beyond the Predefined Reports

Worked conversations showing what the skill enables outside the six
predefined report types — ad-hoc SQL, hand-assembled aggregates fed
into artifact scripts, and hypothesis-driven drilldowns. These examples are
synthetic and intentionally idealized; they emphasize the target analysis flow
over tool friction.

- [Cross-window capacity plan](examples/conversations/cross-window-capacity-plan.md) —
  compare two non-adjacent windows (Black Friday vs. last weekend) to
  characterize a holiday bot surge for capacity planning. Demonstrates
  custom MCP SQL, `compare_posture.py --schema movers` on
  hand-assembled JSON, and explicit confidence framing on a question
  the CLI does not natively support.
- [Anomaly investigation](examples/conversations/anomaly-investigation.md) —
  triage a mid-morning bot-share spike that turns out to be two
  unrelated movers (verified search crawler + unverified credential
  tester) with different remediation paths. Demonstrates hourly-grain
  time-localization, behavior-signature reasoning under
  classifier-spoof risk, domain-scoped `scorecard.py` invocation, and
  split recommendations per entity.
- [SIEM unavailable triage](examples/conversations/siem-unavailable-triage.md) —
  handle a less-clean investigation where posture data is available but
  SIEM/security evidence is missing. Demonstrates fallback to operational
  risk framing, missing-evidence boundaries, and follow-up routing without
  overclaiming malicious intent.

## Common Manual Workflows

Render a saved demo payload:

```bash
uv run --with jinja2 --with markdown-it-py --with bleach \
  python skills/bot-insights/scripts/render_report.py \
  --file skills/bot-insights/examples/scorecard-brief.json \
  --format html \
  --output /tmp/scorecard-brief.html
```

Read the authoritative references before changing report behavior:

- [SKILL.md](SKILL.md) for agent routing, data firewall, and safety rules.
- [references/reporting.md](references/reporting.md) for report workflow,
  supported report types, artifact shapes, and renderer commands.

## Keeping This README Consistent

Use this checklist whenever changing the README:

- Do not add runtime instructions that are not already in [SKILL.md](SKILL.md)
  or one of its referenced files.
- If the trigger scope, report types, data firewall, deployed tables, or
  progressive-disclosure routing changes, update `SKILL.md` first.
- Keep report-type names aligned with
  [references/reporting.md](references/reporting.md).
- Keep directory descriptions aligned with the files actually present in this
  skill directory.
- Keep examples runnable through `uv run`; include explicit `--with`
  dependencies when a script needs packages that are not installed in the
  default environment.
- Prefer links to the authoritative files over duplicated explanations.

When in doubt, make `SKILL.md` precise and keep this README short.
