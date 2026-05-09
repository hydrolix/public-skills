# Bot Insights data-firewall policy

## Context

`bot_insights_capture.py` already implements the mechanism we want as
policy: when Hydrolix credentials resolve from
`~/.config/hydrolix/clusters/<cluster>/*.env` (or environment), it
queries the Hydrolix `/query/` HTTP endpoint directly and writes only
the JSON result to disk. When credentials don't resolve, it emits a
`bot_hydrolix_mcp_query_request.v1` handoff packet (with the
already-validated SQL) and exits with code `42`. The LLM/agent then
runs `run_select_query` for that exact SQL, saves the JSON, and
resumes.

The mechanism is the firewall: when creds are local, no proprietary
query result ever crosses the LLM boundary — the LLM only sees the
deterministic artifact JSON the producer scripts emit. When creds are
absent, the LLM still only sees the SQL it's authorized to run plus
the result of running that SQL.

What's missing is:

1. **Policy guidance**. The skill never says "the LLM MUST NOT call
   `run_select_query` for predefined report data when local creds are
   configured." The current `SKILL.md` mentions MCP gating in passing
   but doesn't name the firewall, the heuristic, or the consequence.
2. **Coverage**. The orchestration in `bot_insights_report.py` only
   wires the capture-script-with-handoff flow for `executive_posture`,
   `control_review`, and `scorecard_brief`. For `soc_triage`,
   `crawler_governance`, and `edge_ops_impact` there is no script-
   orchestrated path, so the policy is documentable but not
   enforceable for those report types — the LLM has no choice but to
   run SQL through MCP, even when local creds exist.
3. **Per-report walkthroughs**. The reference files
   (`scorecard-analysis.md:195`, `soc-analysis.md`) show SQL templates
   and stop at "Use the Hydrolix MCP server or host query tool to run
   them." There is no copy-paste flow showing
   `bot_insights_capture.py` (or a wrapper) running the template
   under both credential states.

This branch closes (1) and (3) through documentation and adds (2) for
`soc_triage` specifically. `crawler_governance` and `edge_ops_impact`
follow the same pattern but are tracked as the next walk-through after
this lands — same sequencing as the SOC rendering work.

## The policy (what we're locking in)

For **predefined report types** (`executive_posture`,
`control_review`, `soc_triage`, `scorecard_brief`,
`crawler_governance`, `edge_ops_impact`):

- **If `~/.config/hydrolix/clusters/<cluster>/*.env` resolves** (or the
  same vars are set in the environment): the LLM MUST NOT call
  `mcp__*__run_select_query` for that report's data. The deterministic
  capture script runs the query directly via the Hydrolix HTTP
  endpoint and writes only the JSON result to a local path. The LLM's
  view is the deterministic artifact JSON the producer scripts emit
  from that result, never the raw query response.
- **If credentials don't resolve** (handoff path): the capture script
  emits a `bot_hydrolix_mcp_query_request.v1` packet and exits `42`.
  The LLM then runs `run_select_query` with exactly the packet's
  `cluster` and `validated_sql`, saves the JSON to the path the
  packet specifies, and resumes the capture/report script with
  `--raw-input <path>`.
- **Exploratory analysis** (broad investigation SQL outside a
  predefined report) is unaffected — the LLM uses Hydrolix MCP / host
  query tools as today. The policy applies only to predefined report
  data.

Why: when creds are local, this creates a firewall between proprietary
query results and the LLM. The LLM sees the validated SQL and the
post-aggregation deterministic artifacts, not the raw query
response. The handoff path keeps the same firewall property at the
cost of one round-trip through MCP — only the validated SQL crosses,
and only because the agent has no other way to reach Hydrolix.

The corollary: any user-facing report that has no script-orchestrated
capture path is operating outside the firewall. We will close those
gaps in priority order, starting with `soc_triage` here.

## What changes

### 1. `skills/bot-insights/SKILL.md` — new "Data Firewall" section

Lead the file's `Triage Flow` block with a `Data Firewall` section
that names the policy explicitly. Keep it short — the per-report
detail belongs in the references. Cover:

- The two credential states and what each implies for the LLM.
- Which scripts run queries (`bot_insights_capture.py`,
  `bot_insights_report.py` via capture) and which don't (`scorecard.py`,
  `attribution.py`, `cache_origin_impact.py`, `compare_posture.py`,
  `compare_delta.py`, `render_report.py`).
- The "exploratory analysis" carve-out, with one sentence on why it
  exists (you can't write a deterministic capture for an open-ended
  investigation).
- The decision rule the LLM applies before running any
  `run_select_query` for a predefined report:
  > 1. Is this a predefined report type? → if no, MCP is fine.
  > 2. Is there a `~/.config/hydrolix/clusters/<cluster>/*.env`
  >    file with `HYDROLIX_HOST` (or `HDX_HOSTNAME`) and a token /
  >    user-password pair that isn't an unresolved `op://`? → if
  >    yes, MCP is forbidden for this data. Use the script.
  > 3. Otherwise, run the script first — only call `run_select_query`
  >    if the script emits a `bot_hydrolix_mcp_query_request.v1`
  >    packet and exits `42`, and only with that packet's exact
  >    `cluster` + `validated_sql`.

The `Query Guardrails` block should pick up a forward-pointer to this
section instead of restating the rule per-script.

### 2. `references/reporting.md` — Workflow Matrix gets concrete

The matrix at lines 71–95 today carries `Capture path` cells that say
things like "Aggregate rows from Hydrolix MCP/host query tool, then
`scorecard.py`; no skill-owned evidence capture yet." Replace with
per-report concrete commands for both credential states. Schema:

```
Report          Creds resolved (firewall on)         No creds (handoff path)
executive_post  bot_insights_report --mode evidence  same; resume with --raw-input
control_review  bot_insights_report --mode evidence  same; resume with --raw-input
scorecard_brief bot_insights_report --mode evidence  same; resume with --raw-input
soc_triage      bot_insights_report --mode evidence  (after wiring; see §4)
                                                     same; resume with --raw-input
crawler_govern  not yet wired — exploratory only,    same — flag explicitly
edge_ops_impact   flag explicitly                    same — flag explicitly
```

Add a "Query Execution Boundary" subsection that restates the policy
in two paragraphs — one for each credential state — and points to
the SKILL.md section for the canonical statement.

### 3. `references/scorecard-analysis.md` — replace the bare-MCP line

Today line 195: `Use the Hydrolix MCP server or host query tool to run
them.` That's the wrong default and pre-dates the firewall. Replace
with:

```
For the predefined report types these templates feed (soc_triage,
scorecard_brief, crawler_governance, edge_ops_impact), prefer the
script-orchestrated path documented in references/reporting.md —
bot_insights_capture.py runs the query directly when local creds
resolve and emits a handoff packet otherwise. Run the templates
directly via Hydrolix MCP only when no script path covers the
report type, or for exploratory analysis outside a predefined
report. See SKILL.md "Data Firewall".
```

The same one-paragraph swap goes into `references/soc-analysis.md`'s
intro (which today implicitly assumes MCP).

### 4. `bot_insights_report.py` — wire `soc_triage` capture orchestration

Today `args.report` only accepts `executive_posture`, `control_review`,
`scorecard_brief`. Add `soc_triage` and orchestrate the same
capture-then-resume flow `scorecard_brief` already follows — the SOC
fixture's input shape is the same `bot_scorecard_artifacts.v1` packet
the brief consumes, so most of the existing scorecard-evidence code
path applies. Spell:

- Extend `--report` `choices` to include `soc_triage`.
- For `soc_triage`, use the same SQL template selection
  (`SCORECARD_ENTITY_SQL`) that `scorecard_brief` uses, but seeded
  from the SIEM/security population (matching the SOC analysis
  reference). The capture script call signature is unchanged.
- The `--mode evidence` packet for `soc_triage` reuses
  `build_scorecard_evidence_packet` with a SOC-specific
  `interpretation_contract` ("security risk triage; do not claim
  malicious without additional artifacts").
- Wrapper title default already lands as "SOC Triage" from the SOC
  rendering branch.
- `crawler_governance` and `edge_ops_impact` stay outside this
  branch's scope — same sequencing as in the SOC rendering plan.

### 5. Optional: enforcement nudge in the capture script

`bot_insights_capture.py` could log a one-line note when running in
"creds resolved" mode, e.g. `auth_mode=bearer firewall=on`. The LLM
sees this in stderr/stdout and can quote it back when reporting the
result. Cheap, hard to miss. Park if it grows beyond one log line —
the policy is a documentation contract, not a runtime enforcement
mechanism.

## Critical files

| File | Purpose |
|------|---------|
| `skills/bot-insights/SKILL.md` (MOD) | Add the `## Data Firewall` section above `## Triage Flow`. Update the `Query Guardrails` block to forward-point rather than restate. |
| `skills/bot-insights/references/reporting.md` (MOD) | Replace the Workflow Matrix's vague "no skill-owned evidence capture yet" cells with concrete commands for both credential states. Add a `Query Execution Boundary` subsection. |
| `skills/bot-insights/references/scorecard-analysis.md` (MOD) | Replace line 195 with the firewall-aware paragraph. Same edit at the top of `soc-analysis.md`. |
| `skills/bot-insights/references/soc-analysis.md` (MOD) | Lead with the firewall-aware capture pointer; the SQL templates stay as-is. |
| `skills/bot-insights/scripts/bot_insights_report.py` (MOD) | Add `soc_triage` to `--report` choices; orchestrate capture/handoff/resume the same way `scorecard_brief` does, with a SOC-lens interpretation contract. |
| `skills/bot-insights/scripts/bot_insights_capture.py` (MOD, optional) | One-line firewall-state log in the summary JSON output (e.g. `"firewall": "on"` when creds resolved). |
| `tests/test_skill_scripts.py` (MOD) | Cover `bot_insights_report.py --report soc_triage --mode evidence` exits 42 with a handoff packet when creds are absent (env stripped); covers `--raw-input` resume produces a wrapper renderable through `render_report.py`. |
| `tests/fixtures/bot_insights_report/soc_triage_*` (NEW, optional) | Fixtures for the SOC orchestration path: synthetic `--raw-input` JSON, expected wrapper shape. Mirror `scorecard_brief` test structure. |

## Out of scope

- Wiring `crawler_governance` and `edge_ops_impact` capture
  orchestration. Tracked as the next walk-through. Both share the
  scorecard-evidence-packet shape, so the increment after this is
  small.
- Changing the credential-resolution logic in
  `bot_insights_capture.py`. The current `~/.config/hydrolix/clusters/`
  + env-var precedence is the contract — codify, don't redesign.
- Enforcing the policy at runtime (e.g. having the capture script
  refuse to run when MCP is detected as already used). The firewall
  is a documented contract; runtime enforcement would need a model of
  what the LLM did, which we don't have.
- Updating the `bot-insights/agents/` files. They inherit the SKILL.md
  guidance.

## Verification

- `SKILL.md` `Data Firewall` section reads top-to-bottom in under a
  minute and answers the question "should I call `run_select_query`
  right now?" with a deterministic yes/no.
- `references/reporting.md` Workflow Matrix has at least one concrete
  command per row in both credential-state columns.
- `bot_insights_report.py --report soc_triage --mode evidence` runs
  end-to-end against the SOC example fixture: with creds → produces a
  `bot_report_evidence.v1` packet locally; without creds → exits 42
  with a `bot_hydrolix_mcp_query_request.v1` packet.
- Existing tests:
  ```
  uv run pytest tests/test_report_engine.py tests/test_skill_scripts.py
  uv run ruff format <edited paths>
  uv run ruff check --fix <edited paths>
  uv run mypy skills/bot-insights/scripts/
  ```
- Spot-check by reading the end-to-end SOC walkthrough in
  `references/reporting.md` from a cold state: a reader who has never
  seen the firewall should be able to produce a SOC report (in either
  credential state) by following the matrix row.
