# edge_ops_impact capture orchestration

> Plan. Branch: `edge-ops-impact-orchestration`. Worktree:
> `.worktrees/edge-ops-impact-orchestration`. Builds on the
> just-shipped `edge_ops_impact` engine port (commit `758b1b3`) by
> wiring the data-firewall capture pipeline for the report — adding
> `edge_ops_impact` to `bot_insights_report.py`'s `--report` choices,
> deterministic SQL builders for entity-grain (`bi_summary_*`) and
> path-grain (`bot_agg_path_*`) queries, two-query orchestration
> following the `control_review` precedent, and a wrapper builder
> that bundles both artifacts into a `bot_report_input.v1` shape the
> engine renderer can consume.

## Context

The just-shipped engine port produces `edge_ops_impact` HTML and
markdown from a `bot_report_input.v1` wrapper that carries a
`bot_scorecard_artifacts.v1` packet (entity-grain) and optionally a
`cache_origin_impact_report.v1` artifact (path-grain). The renderer
suppresses the Top Paths section when the path artifact is absent and
shows entity-grain only.

There is no producer-side orchestration for `edge_ops_impact` yet.
`bot_insights_report.py` knows about `executive_posture`,
`control_review`, `scorecard_brief`, `soc_triage`, and
`crawler_governance` — each follows the same template:

1. SQL builder function (e.g. `scorecard_crawler_sql`)
2. Entity-type mapping (`CRAWLER_ENTITY_SQL`)
3. Dispatch branch in `main()` (`elif args.report == "crawler_governance":`)
4. Capture step through `bot_insights_capture.py` (local creds → JSON;
   no creds → `NEEDS_MCP_EXIT` handoff packet)
5. Post-process the raw JSON through `scorecard.py` to build the
   artifact and wrapper
6. Render via `render_report.py`

`control_review` is the precedent for **two-query** orchestration —
its main posture query runs first, then a separate timeseries query
runs second. The handoff path emits **two** handoff packets in
sequence with `report_context.artifact` annotations
(`"scorecard"` / `"timeseries"`) so the resuming agent knows which
result it's feeding back. Each query is independently gated by the
data firewall.

This plan adapts that two-query pattern for `edge_ops_impact`:

- Query 1: entity-grain against `bi_summary_<granularity>`, processed
  by `scorecard.py`, emits `bot_scorecard_artifacts.v1`.
- Query 2: path-grain against `bot_agg_path_<granularity>`, processed
  by the existing `cache_origin_impact.py` detector, emits
  `cache_origin_impact_report.v1`.
- Both artifacts bundled into a `bot_report_input.v1` wrapper with
  `report_type: edge_ops_impact`.

Path-grain failure (table missing, query error, zero rows) is
non-fatal: emit a warning, continue with entity-grain only. The
renderer already handles the empty-path case.

## What changes

### 1. `skills/bot-insights/scripts/bot_insights_report.py` (MOD)

**Add `edge_ops_impact` to the `--report` choices** (around line
1447):

```python
"--report",
choices=(
    "executive_posture",
    "control_review",
    "scorecard_brief",
    "soc_triage",
    "crawler_governance",
    "edge_ops_impact",
),
```

**Add entity-type mapping** alongside `CRAWLER_ENTITY_SQL` (around
line 319):

```python
EDGE_OPS_ENTITY_SQL = {
    "client_asn": "toString(clientAsn)",
    "request_host": "toString(reqHost)",
    "bot_class": "toString(userAgentCategory)",
}
```

No `_POPULATION_BY_ENTITY` map is needed — edge_ops_impact features
don't gate on a population label the way crawler does.

**Add entity-grain SQL builder** following the
`scorecard_crawler_sql` shape (around line 422). Targets
`bi_summary_<granularity>`. Emits per-entity aggregate rows with the
columns the existing scorecard.py cache_busting + origin_impact
evaluators consume:

- `current_requests`, `baseline_requests`
- `current_cache_miss_pct`, `baseline_cache_miss_pct` (computed via
  `countMergeIf(reqTimeSec >= current_start AND cacheStatus = 'MISS')
  / current_requests * 100`)
- `current_unique_qs`, `baseline_unique_qs` (when summary table
  carries `uniqMerge(query_string_states)` or equivalent — fall back
  to `null` if not present in `bi_summary_*`)
- `current_origin_p95_ms`, `baseline_origin_p95_ms` (when summary
  carries an origin-latency histogram — fall back to `null`)
- `origin_cost_contribution_pct` (computed as this entity's share of
  the cluster-wide origin-pressure proxy: `current_origin_p95_ms *
  current_requests / cluster_total`)

Function signature:

```python
def scorecard_edge_ops_sql(
    database: str,
    start: datetime,
    end: datetime,
    baseline_start: datetime,
    entity_type: str,
    producer_limit: int,
) -> str:
```

Mirror the `LIMIT producer_limit` and `ORDER BY current_requests
DESC` patterns from the crawler SQL. Bail with `SystemExit` when
`entity_type not in EDGE_OPS_ENTITY_SQL`, matching crawler's
behavior at line 432.

**Add path-grain SQL builder** (new helper, sits next to the entity
builder). Targets `bot_agg_path_<granularity>`. Emits the columns
`cache_origin_impact.py` consumes per
`references/cache-origin-impact.md` and the script's `_validated_rows`
schema:

- Dimension columns: `request_path_norm`, `request_host` (optional
  scope filter via `--host` flag), `bot_class` (optional dimension —
  omit for v1, single-host queries are the common case)
- Use wide-form: one row per path with both `current_*` and
  `baseline_*` metric columns. The detector's `_metric_rows` accepts
  this shape directly (no `period` column needed). Match the
  prefix conventions: `current_requests`, `baseline_requests`,
  `current_cache_misses`, `baseline_cache_misses`,
  `current_unique_query_strings`, `baseline_unique_query_strings`,
  `current_origin_p95_ms`, `baseline_origin_p95_ms`. Compute each
  via `countMergeIf(<metric>, reqTimeSec >= current_start)` /
  `countMergeIf(<metric>, reqTimeSec < current_start)`, mirroring
  the entity-grain SQL pattern.

Function signature:

```python
def cache_origin_path_sql(
    database: str,
    start: datetime,
    end: datetime,
    baseline_start: datetime,
    host_filter: str | None,
    producer_limit: int,
) -> str:
```

Default `host_filter=None` queries fleet-wide; when present, scopes
to a specific request_host.

**Add the dispatch branch in `main()`** following the SQL-building
pattern (around line 1639):

```python
elif args.report == "edge_ops_impact":
    sql = scorecard_edge_ops_sql(
        args.database,
        start,
        end,
        baseline_start,
        args.entity_type,
        args.scorecard_limit,
    )
    granularity = choose_granularity(start, end)
    table_used = f"{args.database}.bi_summary_{granularity}"
    compare_schema = None
```

**Extend the post-capture branching** to run the second query after
the entity-grain capture succeeds, mirroring the control_review
timeseries flow at line 1712. Path-grain capture failures (subprocess
nonzero exit other than `NEEDS_MCP_EXIT`, or returned JSON with no
ranked candidates) are logged via `print(f"WARNING: ...",
file=sys.stderr)` and the wrapper proceeds with `path_artifact=None`.

When the entity capture returns a handoff packet, the script must
also emit a separate handoff packet for the path-grain query with
`report_context.artifact = "path"`. This is the two-step handoff —
the agent runs both queries, then re-invokes with two `--raw-input`
files. Match `control_review`'s pattern at line 1742-1763.

**Update `scorecard_reports` set** at line 1549 to include
`edge_ops_impact`:

```python
scorecard_reports = {
    "scorecard_brief",
    "soc_triage",
    "crawler_governance",
    "edge_ops_impact",
}
```

And the entity-value-supporting set at line 1552:

```python
"--entity-value is only supported with --report scorecard_brief, "
"--report soc_triage, --report crawler_governance, or "
"--report edge_ops_impact."
```

And the entity_type validation at line 1562:

```python
elif args.report == "edge_ops_impact" and args.entity_type not in EDGE_OPS_ENTITY_SQL:
    raise SystemExit(
        "--entity-type "
        + args.entity_type
        + " is not supported for edge_ops_impact; use one of "
        + ", ".join(sorted(EDGE_OPS_ENTITY_SQL))
    )
```

**Update the report_context** at line 1698 to include
`edge_ops_impact` in the entity-aware set so handoff packets carry
the right metadata.

**Add an `EDGE_OPS_INTERPRETATION_CONTRACT` and
`EDGE_OPS_TEMPLATE_SECTIONS`** alongside the crawler versions (around
lines 910 / 924). Used by the `evidence` and `template` modes (not
the primary `report` mode, but the constants need to exist to match
the dispatch shape).

**Add the human label mappings** (around lines 1295 and 1320):

```python
"edge_ops_impact": "Edge & Origin Cost Interpretation",
# and:
"edge_ops_impact": "Edge & Origin Cost",
```

### 2. `skills/bot-insights/scripts/bot_insights_report.py` — wrapper builder (NEW function)

Add a `build_edge_ops_impact_wrapper(...)` helper that produces the
`bot_report_input.v1` shape. Reuses the existing wrapper conventions
from `build_soc_triage_wrapper` / `build_crawler_governance_wrapper`
(grep for these to find the precedent). Key fields:

- `schema_version: "bot_report_input.v1"`
- `report_type: "edge_ops_impact"`
- `title`: deterministic format like
  `"Edge & Origin Cost - {scope_label} - {end_date}"`
- `scope_label`: extracted from the scorecard packet's scope
- `artifacts`: list containing the scorecard packet and (when present)
  the path artifact. Path is filtered out when None.
- `analyst_notes`: empty list by default; templated voice is added
  by analyst LLM downstream

### 3. `skills/bot-insights/SKILL.md` (MOD)

Add a one-line entry to the report-types table or the orchestration
section noting that `edge_ops_impact` is now a supported `--report`
choice. Mirror what the crawler-governance branch added (commit
`58ee998 Add crawler_governance capture orchestration`):

```bash
git show 58ee998 -- skills/bot-insights/SKILL.md
```

### 4. `skills/bot-insights/references/edge-ops-analysis.md` (MOD)

Add a "Producer orchestration" subsection naming
`bot_insights_report.py --report edge_ops_impact` as the supported
producer entry point and pointing to `cache-origin-impact.md` for
the path-grain detector contract. Two-query nature is called out.

### 5. Tests in `tests/test_skill_scripts.py` (MOD)

Three new tests, modeled on the crawler-governance pattern (find via
`grep -n "edge_ops_impact\|crawler_governance" tests/test_skill_scripts.py`
once the crawler tests exist):

- `test_bot_insights_report_edge_ops_impact_handoff_packet` — runs
  with no local creds; asserts two handoff packets emitted in
  sequence, first with `report_context.artifact = "scorecard"`,
  second with `report_context.artifact = "path"`. Both carry SQL
  starting with the appropriate table reference (`bi_summary_*` and
  `bot_agg_path_*`).
- `test_bot_insights_report_edge_ops_impact_raw_input_emits_evidence`
  — supplies canned raw JSON for both queries via `--raw-input` and
  `--raw-input-path`; asserts the produced wrapper has both artifacts
  with the right schema versions and the rendered output (asserted
  on the markdown or HTML body) contains both entity-grain queue
  and a Top Paths section.
- `test_bot_insights_report_edge_ops_impact_path_grain_fallback` —
  supplies entity-grain raw JSON only (path query is mocked to
  produce zero rows or fail); asserts the wrapper has only the
  scorecard packet, the warnings list contains the path-grain
  fallback message, and the rendered output has no Top Paths section.

Each test mocks the `bot_insights_capture.py` subprocess by patching
`run` to return canned JSON strings — same pattern crawler tests
use.

### 6. `skills/bot-insights/scripts/bot_insights_report.py` — CLI surface (MOD)

Add a `--raw-input-path` flag mirroring the existing `--raw-input`,
but pointing at the path-grain JSON. When both raw-input flags are
present, the script skips both captures and assembles the wrapper
from the supplied files. When only `--raw-input` is present and
the report is `edge_ops_impact`, treat the path-grain side as
unavailable (warning + omit).

```python
parser.add_argument(
    "--raw-input-path",
    type=str,
    default=None,
    help="Resume edge_ops_impact from a saved path-grain JSON result alongside --raw-input.",
)
```

## Critical files

| File | Purpose |
|------|---------|
| `skills/bot-insights/scripts/bot_insights_report.py` (MOD) | Adds edge_ops_impact to --report choices; adds EDGE_OPS_ENTITY_SQL map, scorecard_edge_ops_sql and cache_origin_path_sql SQL builders, dispatch branch, two-query orchestration, wrapper builder, raw-input-path flag. |
| `skills/bot-insights/SKILL.md` (MOD) | Documents the new --report choice. |
| `skills/bot-insights/references/edge-ops-analysis.md` (MOD) | Names the producer entry point and the two-query pattern. |
| `tests/test_skill_scripts.py` (MOD) | Three new test methods covering happy path, handoff, and path-grain fallback. |

## Reused functions and conventions

- `bot_insights_capture.py` — unchanged; called twice via subprocess
- `scorecard.py` — unchanged; existing cache_busting + origin_impact
  evaluators are what the entity-grain pipeline consumes
- `cache_origin_impact.py` — unchanged; existing detector consumes
  the path-grain JSON we produce
- `render_report.py` — unchanged; engine route for `edge_ops_impact`
  was wired in the prior render branch
- `choose_granularity()`, `sql_ts()`, `HANDOFF_SCHEMA`,
  `NEEDS_MCP_EXIT`, `load_raw_query_result()` — all existing helpers
- Two-query handoff pattern from `control_review` at lines 1712–1763

## Out of scope

- **Renderer changes.** Engine port shipped.
- **New scorecard.py features.** The six existing cache_busting +
  origin_impact evaluators are what gets rendered.
- **Changes to `cache_origin_impact.py`.** Existing detector works
  as-is.
- **A separate path-grain report type.** Path candidates live inside
  edge_ops_impact, not in their own report.
- **Real-time, exact-status, or per-IP grain.** Stay on the
  path-aggregate summary surface per the data-firewall philosophy.
- **Hydrolix MCP server query routing logic.** This script never
  talks to MCP directly; the agent does.
- **Posture / mover artifact integration.** Listed as out-of-scope of
  the render branch and out-of-scope here too.
- **Multi-host scoping for the path query.** v1 uses single
  host_filter or fleet-wide. Multi-host comes later if needed.
- **Origin-cost calibration against real billing.** v1 reports cost
  share via `origin_cost_contribution_pct` as the renderer expects.
  Surfacing real $ or bytes-served requires response-bytes evidence
  which isn't reliably present in `bi_summary_*`.

## Verification

- `uv run pytest tests/test_skill_scripts.py -k edge_ops_impact` —
  three new tests pass.
- `uv run pytest tests/test_report_engine.py tests/test_skill_scripts.py`
  — full suite green; existing tests still pass.
- `uv run ruff format` on the touched files.
- `uv run ruff check --fix` on the same set.
- `uv run mypy skills/bot-insights/scripts/bot_insights_report.py` —
  no errors above the baseline established on `main`.
- **End-to-end smoke test (manual, against acme):**
  ```bash
  uv run python skills/bot-insights/scripts/bot_insights_report.py \
    --cluster acme \
    --database <acme-db> \
    --report edge_ops_impact \
    --entity-type request_host \
    --start <iso-ts> \
    --end <iso-ts> \
    --format html \
    --output /tmp/acme-edge-ops-impact.html
  ```
  Verify: the produced HTML has the engine markup (kicker, queue
  table, evidence cards, Top Paths section if path-grain succeeded
  or absent if it didn't), and the data-firewall trail shows two
  separate Hydrolix queries (no LLM↔database raw rows).
