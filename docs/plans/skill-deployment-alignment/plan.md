# Bot Insights skill ↔ deployment alignment

> Plan. Branch: `skill-deployment-alignment`. Worktree:
> `.worktrees/skill-deployment-alignment`. Reconciles the Bot Insights
> skill's table references with what's actually deployed on the
> Hydrolix clusters. Live probes against `demo.trafficpeak.live` and
> `acme` reveal substantial drift: several table families
> referenced across the skill don't exist on any live cluster, one
> table that the skill targets fails on acme because SIEM
> isn't deployed there yet, and the cac-tools 1.1.9 bundle's renamed
> `bot_siem_policy_summary_*` table isn't actually installed on
> either cluster.

## Context

A real-data run against acme during the just-shipped
`edge_ops_impact` orchestration branch surfaced that the path-grain
summary table the renderer assumed (`bot_agg_path_*`) doesn't exist
on the cluster. The graceful fallback added there made the run
succeed (entity-grain only), but the broader question — what tables
does the skill assume exist that actually don't — turned up
substantial drift.

Live probes via `bot_insights_capture.py` against both clusters
(plus inspection of the cac-tools 1.1.9 bundle definitions) show:

| Table family | demo.trafficpeak.live | acme | cac-tools 1.1.9 bundle |
|---|---|---|---|
| `bi_summary_*` | ✓ | ✓ | ✓ |
| `bi_siem_policy_summary_*` | ✓ | ✗ | ✗ |
| `bot_siem_policy_summary_*` | ✗ | ✗ | ✓ (defined, not installed) |
| `bot_agg_path_*` | ✗ | ✗ | ✗ |
| `bot_agg_resource_*`, `bot_agg_ua_*` | ✗ | ✗ | ✗ |
| `bot_detection`, `bot_detection_siem` | ✗ | ✗ | ✗ |

**Decisions confirmed with the user:**

- **SIEM**: acme is expected to get SIEM tables eventually.
  Keep the existing `bi_siem_policy_summary_*` reference (it matches
  the demo deployment, which is the only live cluster currently
  serving SIEM). SOC's failure on clusters without SIEM should be
  graceful and self-explanatory rather than a raw HTTP 400.
- **Path-grain / resource / UA / request-level tables**: not
  deployed anywhere, no roadmap. Remove references throughout the
  skill — they teach the LLM agent to write SQL against tables that
  don't exist.

## What changes

### 1. `skills/bot-insights/scripts/bot_insights_report.py` (MOD)

**SOC graceful fallback for missing SIEM** — mirror the path-grain
fallback added to `edge_ops_impact`. Wrap the SOC capture call in
try/except SystemExit; on failure, print a warning naming the
likely cause (`bi_siem_policy_summary_<granularity>` not deployed on
the cluster) and exit cleanly. The script can't produce a SOC
triage report without SIEM data, so this is exit-with-warning
rather than degrade-and-continue.

**Edge_ops_impact path-grain → opt-in** — the path-grain query
fails on every cluster today. Running it by default wastes one
query per report. Add `--include-paths` CLI flag (default `False`);
when False, skip the path-grain capture entirely. When True, run
the path-grain query with the existing graceful-fallback shim. The
renderer template unchanged.

**Remove `bot_detection*` references** — search for any remaining
`bot_detection` or `bot_detection_siem` references in this script;
none should be in deterministic SQL (those tables aren't deployed),
but evidence packets and template prose may mention them. Audit and
remove.

### 2. `skills/bot-insights/scripts/cache_origin_impact.py` (MOD)

The detector script defines `SUPPORTED_PATH_TABLES` (or equivalent
constants) and example queries naming `bot_agg_path_day`,
`bot_agg_path_hour`, `bot_agg_path_minute`. Since none of these
tables exist anywhere:

- **Keep the script as-is structurally** — it consumes pre-aggregated
  JSON, not a live Hydrolix query. It's still usable when path-grain
  aggregates eventually exist.
- **Mark the supported-tables list as aspirational** with a clear
  module-level docstring note: "These tables are not currently
  deployed on any production cluster. The detector is wired and
  tested for the day these aggregates are installed."
- **Remove all `bot_agg_path_*` example invocations** from the
  module docstring and any inline comments — examples should use
  pasted aggregate JSON, not non-existent tables.

### 3. `skills/bot-insights/scripts/attribution.py` (MOD)

Grep for `bot_detection`, `bot_agg_*`, references; replace example
SQL with summary-table equivalents or remove the example block if no
deployed table can satisfy it. (May be a no-op — verify with `grep
-n bot_detection skills/bot-insights/scripts/attribution.py` first.)

### 4. `skills/bot-insights/SKILL.md` (MOD)

Remove or rewrite mentions of:
- `bot_detection`, `bot_detection_siem` — request-level tables that
  don't exist
- `bot_agg_path_*`, `bot_agg_resource_*`, `bot_agg_ua_*` — path /
  resource / UA aggregates that don't exist

Where the SKILL.md decision-routing tables enumerate the surfaces
("Cache busting, cache misses, origin pressure" → look at X), update
to point only at `bi_summary_*` and `bi_siem_policy_summary_*`
(where applicable) since those are the only deployed tables.

Add a brief "Deployment availability" section that explains the
universal-vs-cluster-specific landscape: `bi_summary_*` is universal,
`bi_siem_policy_summary_*` requires a SIEM-enabled cluster, and
several aggregates referenced in older skill iterations are not
currently deployed.

### 5. Reference docs (MOD)

Audit each file for `bot_agg_path`, `bot_agg_resource`, `bot_agg_ua`,
`bot_detection`, `bot_detection_siem` references and either remove
the section or replace with `bi_summary_*`-based equivalents:

- `skills/bot-insights/references/scorecard-analysis.md`
- `skills/bot-insights/references/edge-ops-analysis.md`
- `skills/bot-insights/references/soc-analysis.md`
- `skills/bot-insights/references/cache-origin-impact.md`
- `skills/bot-insights/references/seo-analysis.md`
- `skills/bot-insights/references/summary-tables.md` — the table
  inventory; mark non-deployed tables with a clear "NOT CURRENTLY
  DEPLOYED" suffix rather than removing them entirely (this file
  serves as the inventory; keeping the row with the warning
  preserves the design intent without misleading the agent)

Where a reference doc has example SQL that targets a non-deployed
table, replace with a deployed equivalent if one exists. If no
deployed table can satisfy the example, remove the section with a
brief explanation: "Path-grain queries are not currently supported;
use entity-grain `bi_summary_*` queries instead."

### 6. Tests (MOD)

- `tests/test_skill_scripts.py` — if any tests reference the
  non-deployed table names in canned SQL strings (e.g., to verify
  the SQL builder emits the right table), update them to align with
  the new opt-in path-grain behavior. The handoff-packet tests for
  `edge_ops_impact` will need to be adapted: the path-grain handoff
  test should now drive the `--include-paths` code path.
- Add a new test confirming SOC's graceful failure when the SIEM
  capture subprocess fails:
  `test_bot_insights_report_soc_triage_missing_siem_table_graceful_error`
  — mocks the SOC capture to return HTTP 400 with the "namespace
  does not exist" error; asserts the script exits with a clear
  warning naming the table, not a raw subprocess error.

### 7. End-to-end smoke tests (MANUAL, post-merge)

After the branch lands:
- Confirm `scorecard_brief` runs against demo.trafficpeak.live and
  acme (both have `bi_summary_*`).
- Confirm `soc_triage` runs against demo.trafficpeak.live (has SIEM)
  and exits gracefully on acme (no SIEM) with the new
  warning.
- Confirm `crawler_governance` runs against both.
- Confirm `edge_ops_impact` runs against both with `--include-paths`
  NOT set (default); a separate run with `--include-paths` still
  produces a graceful path-grain warning.
- Confirm `executive_posture` runs against both.

This is a manual gate — not codified as a pytest — because it
requires live cluster access.

## Critical files

| File | Purpose |
|------|---------|
| `skills/bot-insights/scripts/bot_insights_report.py` (MOD) | SOC graceful fallback; edge_ops_impact `--include-paths` opt-in flag. |
| `skills/bot-insights/scripts/cache_origin_impact.py` (MOD) | Mark path-grain tables as aspirational; remove example invocations. |
| `skills/bot-insights/scripts/attribution.py` (MOD) | Remove `bot_detection*` references if any remain. |
| `skills/bot-insights/SKILL.md` (MOD) | Remove non-deployed table references; add Deployment Availability section. |
| `skills/bot-insights/references/scorecard-analysis.md` (MOD) | Remove or rewrite `bot_agg_*` / `bot_detection*` sections. |
| `skills/bot-insights/references/edge-ops-analysis.md` (MOD) | Same. |
| `skills/bot-insights/references/soc-analysis.md` (MOD) | Same. |
| `skills/bot-insights/references/cache-origin-impact.md` (MOD) | Same. |
| `skills/bot-insights/references/seo-analysis.md` (MOD) | Same. |
| `skills/bot-insights/references/summary-tables.md` (MOD) | Mark non-deployed tables with "NOT CURRENTLY DEPLOYED" suffix in the inventory. |
| `tests/test_skill_scripts.py` (MOD) | Adapt edge_ops_impact path-grain tests to `--include-paths`; add SOC graceful-error test. |

## Reused functions and conventions

- `try/except SystemExit` graceful-fallback pattern from the
  edge_ops_impact orchestration's path-grain capture (just-shipped).
- `argparse.add_argument(..., default=False, action="store_true")` for
  the new `--include-paths` flag.
- Existing `WARNING: <reason>` stderr convention for non-fatal
  capture failures.

## Out of scope

- **Adding new table references**: this is a removal/qualification
  pass, not a feature addition.
- **Deploying the missing tables**: that's cac-tools / bundle
  infrastructure work, not skill work.
- **Renaming `bi_siem_policy_summary_*` to match the cac-tools
  1.1.9 bundle's `bot_siem_policy_summary_*`**: the bundle name
  isn't deployed anywhere, so renaming the skill would break the
  one cluster where SIEM actually works (demo.trafficpeak.live).
- **Runtime table introspection**: out of scope; if needed later,
  add as its own branch.
- **Migrating acme to install SIEM tables**: infrastructure,
  not skill-level.
- **Adding executive_posture / control_review-specific changes**:
  those report types reference only `bi_summary_*`, which is
  universally deployed — no alignment work needed.
- **Refactoring `bot_insights_report.py` for size/complexity**:
  separate tech-debt branch.

## Verification

- `uv run pytest tests/test_report_engine.py tests/test_skill_scripts.py`
  — must remain at 367 passed (plus the new SOC graceful-error
  test); 4 skipped.
- `uv run ruff format` and `uv run ruff check` clean on touched
  files.
- `uv run mypy skills/bot-insights/scripts/bot_insights_report.py
  skills/bot-insights/scripts/cache_origin_impact.py
  skills/bot-insights/scripts/attribution.py` — no errors above
  baseline.
- `grep -r "bot_agg_path\|bot_agg_resource\|bot_agg_ua\|bot_detection"
  skills/bot-insights/` returns only references in
  `summary-tables.md` (marked as "NOT CURRENTLY DEPLOYED") — no
  other live references.
- Manual smoke tests against demo.trafficpeak.live + acme
  (post-merge; see section 7).
