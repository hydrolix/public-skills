"""Snapshot and unit tests for the Bot Insights report engine.

Run all tests:
    uv run pytest tests/test_report_engine.py -v

Update snapshots after an intentional rendering change:
    REPORT_ENGINE_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_report_engine.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT / "skills/bot-insights/scripts/report_engine"
RENDER_PY = ENGINE_DIR / "render.py"
FIXTURES = Path(__file__).parent / "fixtures/report_engine"
SNAPSHOTS = Path(__file__).parent / "snapshots/report_engine"

# Make charts/findings/theme importable for direct unit tests.
# (markdown.py needs markdown_it + bleach which may be absent — those tests use importorskip.)
sys.path.insert(0, str(ENGINE_DIR.parent))

UPDATE_SNAPSHOTS = os.environ.get("REPORT_ENGINE_UPDATE_SNAPSHOTS") == "1"

VOLATILE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Footer "Generated 2026-05-09 12:34 UTC ·" — render time changes per run.
    (re.compile(r"Generated \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC"), "Generated <FROZEN>"),
)


def _normalize(html: str) -> str:
    """Strip render-time volatility so snapshots are stable across runs."""
    for pattern, replacement in VOLATILE_PATTERNS:
        html = pattern.sub(replacement, html)
    return html


def _render(artifact: Path, *extra: str) -> str:
    """Invoke render.py via uv and return the rendered HTML string."""
    if shutil.which("uv") is None:
        pytest.skip("uv not available")
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
        out_path = Path(f.name)
    try:
        subprocess.run(
            [
                "uv",
                "run",
                "--quiet",
                str(RENDER_PY),
                "--artifact",
                str(artifact),
                "--out",
                str(out_path),
                *extra,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return out_path.read_text()
    finally:
        out_path.unlink(missing_ok=True)


def _assert_snapshot(actual: str, snapshot_path: Path) -> None:
    """Compare against a committed snapshot, or write one on first run."""
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    if UPDATE_SNAPSHOTS or not snapshot_path.exists():
        snapshot_path.write_text(actual)
        if not UPDATE_SNAPSHOTS:
            pytest.skip(f"wrote initial snapshot to {snapshot_path}")
        return
    expected = snapshot_path.read_text()
    if actual != expected:
        diff_path = snapshot_path.with_suffix(".html.actual")
        diff_path.write_text(actual)
        pytest.fail(
            f"snapshot mismatch vs {snapshot_path}.\n"
            f"actual written to {diff_path} for inspection.\n"
            f"if the change is intentional, update with: "
            f"REPORT_ENGINE_UPDATE_SNAPSHOTS=1 uv run pytest "
            f"{Path(__file__).relative_to(ROOT)} -v"
        )


def test_scorecard_brief_artifact_full():
    artifact = FIXTURES / "scorecard_brief_acme_artifact.json"
    snapshot = SNAPSHOTS / "scorecard_brief_acme_full.html"
    actual = _normalize(_render(artifact))
    _assert_snapshot(actual, snapshot)


def test_scorecard_brief_artifact_brief():
    artifact = FIXTURES / "scorecard_brief_acme_artifact.json"
    snapshot = SNAPSHOTS / "scorecard_brief_acme_brief.html"
    actual = _normalize(_render(artifact, "--mode", "brief"))
    _assert_snapshot(actual, snapshot)


def test_scorecard_brief_wrapper_full():
    wrapper = FIXTURES / "scorecard_brief_acme_wrapper.json"
    snapshot = SNAPSHOTS / "scorecard_brief_acme_wrapper.html"
    actual = _normalize(_render(wrapper))
    _assert_snapshot(actual, snapshot)


def test_scorecard_brief_wrapper_brief():
    wrapper = FIXTURES / "scorecard_brief_acme_wrapper.json"
    snapshot = SNAPSHOTS / "scorecard_brief_acme_wrapper_brief.html"
    actual = _normalize(_render(wrapper, "--mode", "brief"))
    _assert_snapshot(actual, snapshot)


# ---- Executive posture (Bot & Edge Movement) --------------------------------


def test_executive_posture_full_wrapper():
    """Full wrapper with posture + mover + scorecards; mover concentration
    drives traffic-weighted lead and `requests` becomes top metric."""
    wrapper = FIXTURES / "executive_posture_full.json"
    snapshot = SNAPSHOTS / "executive_posture_full.html"
    actual = _normalize(_render(wrapper))
    _assert_snapshot(actual, snapshot)
    # Spot checks the snapshot doesn't enforce by itself.
    assert "Bot &amp; Edge Movement" in actual
    assert "Total requests up" in actual
    assert "covers 87" in actual  # mover share clause
    assert "Investigate" in actual
    assert "ASN 64500" in actual


def test_executive_posture_no_movers():
    """Mover artifact absent → headline falls back to direction + magnitude
    only, no contribution clause appended."""
    wrapper = FIXTURES / "executive_posture_no_movers.json"
    snapshot = SNAPSHOTS / "executive_posture_no_movers.html"
    actual = _normalize(_render(wrapper))
    _assert_snapshot(actual, snapshot)
    # No mover banner element (its CSS class still appears inside the
    # embedded stylesheet — check for the rendered <div> instead) and no
    # "covers N% of the increase" clause in prose.
    assert '<div class="shared-signal-banner"' not in actual
    assert "covers 87" not in actual
    # The actions section still surfaces the bot_share_pct and rate-related
    # actions when their thresholds trigger.
    assert "Bot share" in actual


def test_executive_posture_thin_coverage():
    """All metrics low confidence / one with unknown direction → caveat
    fires; metric rows carry confidence chips."""
    wrapper = FIXTURES / "executive_posture_thin_coverage.json"
    snapshot = SNAPSHOTS / "executive_posture_thin_coverage.html"
    actual = _normalize(_render(wrapper))
    _assert_snapshot(actual, snapshot)
    assert "Coverage is thin" in actual
    assert "Real movement may be larger than the visible delta" in actual
    assert "Low confidence" in actual
    # Insufficient data pill should be non-zero (one metric has unknown
    # direction).
    assert "Insufficient data" in actual


# ---- SOC triage --------------------------------------------------------------


def test_soc_triage_full_wrapper():
    """Full wrapper bundling a packet of two ranked ASNs. The top entity
    has a security_evidence primary domain and 5 triggered rules; the
    second entity has only one triggered SIEM rule (Watch). Caveat fires
    on missing-input ratio."""
    wrapper = FIXTURES / "soc_triage_full.json"
    snapshot = SNAPSHOTS / "soc_triage_full.html"
    actual = _normalize(_render(wrapper))
    _assert_snapshot(actual, snapshot)
    # Title and lead.
    assert "SOC Triage — www.example.com, ASN risk queue" in actual
    assert "ASN 64500" in actual
    assert "bad-bot share 65%" in actual
    assert "SIEM evidence present" in actual
    # Caveat fires on the example's high missing-input ratio.
    assert "Real risk may be higher than the score implies" in actual
    # Verdict strip mutes the zero-count pills.
    assert "pill-muted" in actual
    # Security evidence cards render for the Assign entity, with both
    # the security and supporting movement blocks populated.
    assert '<article class="sec-evidence-card' in actual
    assert ">Security signals<" in actual
    assert ">Supporting movement signals<" in actual
    # Domain score matrix renders both active domains.
    assert '<table class="data-table domain-matrix">' in actual
    # Full wrapper is NOT degraded.
    assert '<div class="degraded-banner"' not in actual


def test_soc_triage_index_only_degraded():
    """Wrapper carries the ranking index but no scorecards. Degraded
    banner fires; queue table renders rows from the index; no security
    cards or domain matrix."""
    wrapper = FIXTURES / "soc_triage_index_only.json"
    snapshot = SNAPSHOTS / "soc_triage_index_only.html"
    actual = _normalize(_render(wrapper))
    _assert_snapshot(actual, snapshot)
    assert '<div class="degraded-banner"' in actual
    assert "ASN 64500" in actual
    assert "ASN 64600" in actual
    # Domain matrix and security cards are absent in degraded mode.
    assert '<table class="data-table domain-matrix">' not in actual
    assert ">Security signals<" not in actual
    assert '<article class="sec-evidence-card' not in actual


def test_soc_triage_single_entity():
    """N=1 wrapper with full per-rule data plus entity_metrics. Triage
    strip reads as singular; traffic-share clause appears in the lead
    because every scorecard carries current_requests."""
    wrapper = FIXTURES / "soc_triage_single_entity.json"
    snapshot = SNAPSHOTS / "soc_triage_single_entity.html"
    actual = _normalize(_render(wrapper))
    _assert_snapshot(actual, snapshot)
    assert "1 of 1 ASN" in actual
    assert "covers 100% of fleet requests" in actual
    # Singular noun in the verdict-strip rationale ("1 ASN needs ...").
    assert "1 ASN needs analyst attention" in actual
    # Single-entity should still render the queue + cards + matrix.
    assert "ASN 64500" in actual


# ---- Crawler governance ------------------------------------------------------


def test_crawler_governance_full_wrapper():
    """Full wrapper bundling a packet of three ranked AI categories. The
    top entity has a crawler_governance primary domain with all six
    crawler-governance rules triggered plus a movement supporting rule;
    the second entity has three crawler-governance triggers; the third
    only one. Verdict pills mute zero counts."""
    wrapper = FIXTURES / "crawler_governance_full.json"
    snapshot = SNAPSHOTS / "crawler_governance_full.html"
    actual = _normalize(_render(wrapper))
    _assert_snapshot(actual, snapshot)
    assert "Crawler Governance — www.example.com, AI category health queue" in actual
    assert "Ai training" in actual
    assert "80 governance-surface failures" in actual
    # Crawler evidence cards render for the Assign entities, with both
    # the crawler-governance and supporting blocks populated.
    assert '<article class="sec-evidence-card' in actual
    assert ">Crawler-governance signals<" in actual
    assert ">Supporting signals<" in actual
    # Domain score matrix renders both active domains (crawler + movement).
    assert '<table class="data-table domain-matrix">' in actual
    # Full wrapper is NOT degraded.
    assert '<div class="degraded-banner"' not in actual


def test_crawler_governance_index_only_degraded():
    """Wrapper carries the ranking index but no scorecards. Degraded
    banner fires; queue table renders rows from the index; no crawler
    cards or domain matrix."""
    wrapper = FIXTURES / "crawler_governance_index_only.json"
    snapshot = SNAPSHOTS / "crawler_governance_index_only.html"
    actual = _normalize(_render(wrapper))
    _assert_snapshot(actual, snapshot)
    assert '<div class="degraded-banner"' in actual
    assert "Ai training" in actual
    assert "Search crawler" in actual
    # Domain matrix and crawler cards are absent in degraded mode.
    assert '<table class="data-table domain-matrix">' not in actual
    assert ">Crawler-governance signals<" not in actual
    assert '<article class="sec-evidence-card' not in actual


def test_crawler_governance_single_entity():
    """N=1 wrapper with full per-rule data plus entity_metrics. Triage
    strip reads as singular; traffic-share clause appears in the lead
    because every scorecard carries current_requests."""
    wrapper = FIXTURES / "crawler_governance_single_entity.json"
    snapshot = SNAPSHOTS / "crawler_governance_single_entity.html"
    actual = _normalize(_render(wrapper))
    _assert_snapshot(actual, snapshot)
    assert "1 of 1 AI category" in actual
    assert "covers 100% of fleet requests" in actual
    # Singular noun in the verdict-strip rationale.
    assert "1 AI category needs analyst attention" in actual
    # Single-entity should still render the queue + cards.
    assert "Ai training" in actual


# ---- Edge ops impact ---------------------------------------------------------


def test_edge_ops_impact_full_wrapper():
    """Full wrapper bundling a packet of three ranked ASNs and a path-grain
    cache_origin_impact_report. Top two entities carry origin_cost_contribution_pct,
    so the cost-share headline fires; path candidates trigger the top-paths section."""
    wrapper = FIXTURES / "edge_ops_impact_full.json"
    snapshot = SNAPSHOTS / "edge_ops_impact_full.html"
    actual = _normalize(_render(wrapper))
    _assert_snapshot(actual, snapshot)
    # Cost-share headline assertions
    assert "concentrate" in actual
    assert "% of origin pressure" in actual
    # Top-paths section
    assert '<table class="data-table path-candidates-table">' in actual
    # Edge & origin block label (NOT "Crawler-governance signals")
    assert ">Edge &amp; origin signals<" in actual
    assert ">Crawler-governance signals<" not in actual
    # Evidence cards exist
    assert '<article class="sec-evidence-card' in actual
    # Domain matrix renders cache_busting + origin_impact at minimum
    assert '<table class="data-table domain-matrix">' in actual
    # Full wrapper is NOT degraded
    assert '<div class="degraded-banner"' not in actual
    assert "ASN 64500" in actual
    assert "/api/v1/pricing" in actual


def test_edge_ops_impact_index_only_degraded():
    """Wrapper carries the ranking index but no scorecards and no path
    artifact. Degraded banner fires; queue table renders rows from the
    index; no edge cards, no top-paths section, no domain matrix."""
    wrapper = FIXTURES / "edge_ops_impact_index_only.json"
    snapshot = SNAPSHOTS / "edge_ops_impact_index_only.html"
    actual = _normalize(_render(wrapper))
    _assert_snapshot(actual, snapshot)
    assert '<div class="degraded-banner"' in actual
    assert "ASN 64500" in actual
    assert "ASN 64600" in actual
    assert '<table class="data-table domain-matrix">' not in actual
    assert ">Edge &amp; origin signals<" not in actual
    assert '<article class="sec-evidence-card' not in actual
    # No path-candidates table when path artifact absent
    assert '<table class="data-table path-candidates-table">' not in actual


def test_edge_ops_impact_single_entity_no_paths():
    """N=1 wrapper with full per-rule data, entity_metrics, and no path
    artifact. Cost-share lens does NOT fire (origin_cost_contribution_pct
    omitted on the triggered rules), so the rule-based fallback headline
    fires; traffic-share clause appears."""
    wrapper = FIXTURES / "edge_ops_impact_single_entity_no_paths.json"
    snapshot = SNAPSHOTS / "edge_ops_impact_single_entity_no_paths.html"
    actual = _normalize(_render(wrapper))
    _assert_snapshot(actual, snapshot)
    # Cost-share clause should NOT appear (origin_cost_contribution_pct absent)
    assert "% of origin pressure" not in actual
    # Traffic-share clause should appear (entity_metrics carries current_requests)
    assert "covers 100% of fleet requests" in actual
    # Top-paths section absent
    assert '<table class="data-table path-candidates-table">' not in actual


# ---- XSS guard ---------------------------------------------------------------


def test_xss_in_analyst_notes_is_scrubbed():
    """Malicious analyst_notes must not survive the markdown→bleach pipeline.

    Safety is about what the browser EXECUTES, not literal substrings — escaped
    text like ``&lt;script&gt;`` is harmless and may legitimately appear inside
    rendered notes. Assertions look for live tag/attribute patterns only.
    """
    fixture = FIXTURES / "scorecard_brief_acme_malicious_notes.json"
    actual = _render(fixture)
    lower = actual.lower()

    # Live dangerous tags (escaped occurrences are fine).
    for tag in ("<script", "<iframe", "<img ", "<img>", "<object", "<embed"):
        assert tag not in lower, f"live {tag!r} tag survived"

    # Event-handler attributes inside any real tag: <tagname ... on*=
    # The escaped form (&lt;img ... onerror=…&gt;) does not match this pattern.
    assert not re.search(r"<\w+[^>]*\son\w+\s*=", lower), (
        "on*= event-handler attribute on a live tag survived"
    )

    # javascript: URLs inside live href/src attributes.
    assert not re.search(r"""(href|src)\s*=\s*['"]?\s*javascript:""", lower), (
        "javascript: URL in href/src survived"
    )

    # Only the template's own <style> should exist (one).
    assert lower.count("<style") == 1, (
        f"expected 1 <style> (template own), found {lower.count('<style')}"
    )

    # Safe content from the same notes SHOULD appear.
    assert "apihub.acme.com" in actual
    assert "the docs" in actual.lower()
    assert "https://example.com/docs" in actual

    # The note contained `# Top-level header` — must be demoted to h2.
    assert "Top-level header" in actual
    h1_count = lower.count("<h1>")
    assert h1_count == 1, f"expected exactly 1 <h1> (page title), found {h1_count}"


# ---- Pure helper unit tests --------------------------------------------------


def test_bullet_chart_svg_basic():
    from report_engine.charts import bullet_chart_svg

    svg = bullet_chart_svg(actual=85, comparison=70)
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    # Three band rectangles + one actual rectangle = at least 4 rects
    assert svg.count("<rect") >= 4
    # Comparison tick is a <line>
    assert "<line" in svg


def test_bullet_chart_svg_clamps_oob():
    from report_engine.charts import bullet_chart_svg

    # Out-of-range values must clamp, not crash or produce negative widths.
    svg = bullet_chart_svg(actual=150, comparison=-5)
    assert "<svg" in svg
    assert 'width="-' not in svg
    assert 'x1="-' not in svg


def test_slopegraph_svg_empty_returns_empty():
    from report_engine.charts import slopegraph_svg

    assert slopegraph_svg([]) == ""


def test_slopegraph_svg_renders_pairs():
    from report_engine.charts import slopegraph_svg

    entities = [
        {
            "entity": "a.example",
            "score": 80,
            "delta": -10,
        },  # improved (was 90 → 80? no — score went down)
        {"entity": "b.example", "score": 95, "delta": 5},  # was 90, now 95
        {"entity": "c.example", "score": 70, "delta": -20},
    ]
    svg = slopegraph_svg(entities, label_left="baseline", label_right="current")
    assert "a.example" in svg
    assert "baseline" in svg
    assert "current" in svg
    # Each entity → 2 dots + 1 line + 1 label = 4 elements minimum
    assert svg.count("<circle") == 6  # 3 entities × 2 dots
    assert svg.count("<line") == 3


def test_score_gauge_svg_band_zones():
    from report_engine.charts import score_gauge_svg

    svg = score_gauge_svg(85)
    # Three arc-zone strokes (escalate / monitor / observe) + 1 pointer line
    assert svg.count("<path") == 3
    # Big number rendered
    assert ">85<" in svg
    # Band label rendered
    assert "observe" in svg


def test_findings_shared_signal_when_one_rule_dominates():
    from report_engine.findings import build_scorecard_brief_findings
    from collections import Counter

    # Synthetic: 5 hosts, all with cache_miss_rate_high triggered
    scorecards = [
        {
            "rule_results": [
                {
                    "name": "cache_miss_rate_high",
                    "status": "triggered",
                    "domain": "cache_busting",
                    "points": 10,
                },
                {
                    "name": "querystring_diversity_high",
                    "status": "missing_input",
                    "domain": "cache_busting",
                    "points": 0,
                },
            ],
        }
        for _ in range(5)
    ]
    coverage = {
        "cache_busting": {"triggered": 5, "missing_input": 5, "evaluated_zero": 0},
    }
    domain_counts = Counter({"cache_busting": 5})

    findings = build_scorecard_brief_findings(
        scorecards,
        n_total=5,
        n_with_triggers=5,
        n_clean=0,
        n_moved=0,
        domain_counts=domain_counts,
        coverage=coverage,
    )

    assert len(findings) >= 1
    top = findings[0]
    assert top.finding_id == "shared_signal"
    assert "5 hosts" in top.title
    assert "investigate as one issue" in top.title


def test_findings_apply_overrides_replaces_headline():
    from report_engine.findings import Finding, apply_finding_overrides

    findings = [
        Finding(finding_id="shared_signal", title="orig title", body="b"),
        Finding(finding_id="no_movement", title="orig title 2", body="b2"),
    ]
    overrides = '[{"finding_id": "shared_signal", "headline": "Exec rewrite"}]'
    result = apply_finding_overrides(findings, overrides)
    assert result[0].headline == "Exec rewrite"
    assert result[1].headline is None  # untouched


def test_findings_apply_overrides_ignores_malformed():
    from report_engine.findings import Finding, apply_finding_overrides

    findings = [Finding(finding_id="x", title="t", body="b")]
    # Malformed JSON, missing fields, wrong types — all silently ignored.
    for bad in (
        None,
        "",
        "not json",
        "{}",
        "[]",
        '[{"finding_id": "x"}]',  # no headline
        '[{"headline": "lone"}]',  # no finding_id
        '[{"finding_id": "x", "headline": ""}]',  # empty headline
        "42",
    ):  # not a list
        result = apply_finding_overrides(
            [Finding(finding_id="x", title="t", body="b")], bad
        )
        assert result[0].headline is None, f"bad input {bad!r} produced override"


# ---- Markdown helper (gated on optional deps) --------------------------------


def test_markdown_render_safe_strips_scripts():
    pytest.importorskip("markdown_it")
    pytest.importorskip("bleach")
    from report_engine.markdown import render_safe

    out = str(render_safe("Hello\n\n<script>alert('x')</script>\n\n**bold**"))
    assert "<script" not in out
    assert "<strong>bold</strong>" in out


def test_markdown_render_safe_demotes_h1():
    pytest.importorskip("markdown_it")
    pytest.importorskip("bleach")
    from report_engine.markdown import render_safe

    out = str(render_safe("# Top header"))
    assert "<h1>" not in out
    assert "<h2>Top header</h2>" in out


def test_markdown_render_safe_blocks_javascript_links():
    pytest.importorskip("markdown_it")
    pytest.importorskip("bleach")
    from report_engine.markdown import render_safe

    out = str(render_safe("[click](javascript:alert('x'))"))
    assert "javascript:" not in out


def test_markdown_render_safe_allows_https_links():
    pytest.importorskip("markdown_it")
    pytest.importorskip("bleach")
    from report_engine.markdown import render_safe

    out = str(render_safe("[docs](https://example.com)"))
    assert 'href="https://example.com"' in out


# ---------------------------------------------------------------------------
# humanize / deltas — symbol consolidation in M1.1
# ---------------------------------------------------------------------------


def test_humanize_rule_label_parts_known_signal_returns_explicit_pair():
    from report_engine.humanize import rule_label_parts

    assert rule_label_parts("querystring_diversity_high") == (
        "Query String Diversity",
        "High",
    )
    assert rule_label_parts(
        "querystring_diversity_with_high_miss_rate"
    ) == ("Query String Diversity", "With High Miss Rate")


def test_humanize_rule_label_parts_unknown_falls_back_to_display_label():
    from report_engine.humanize import rule_label_parts

    assert rule_label_parts("unknown_signal_shape") == (
        "Unknown Signal Shape",
        "",
    )


def test_humanize_rule_label_parts_preserves_acronyms():
    from report_engine.humanize import rule_label_parts

    axis, condition = rule_label_parts("siem_blocked_present")
    assert axis == "SIEM Blocked Requests"
    assert condition == "Present"


def test_humanize_human_metric_name_known_returns_label():
    from report_engine.humanize import human_metric_name

    assert human_metric_name("requests") == "Total requests"
    assert human_metric_name("bot_share_pct") == "Bot share"


def test_humanize_human_metric_name_unknown_returns_raw_text():
    """Identity fallback is load-bearing — the legacy markdown escape
    test in test_skill_scripts expects user-controlled metric names to
    pass through unchanged so downstream escaping sees them verbatim.
    """
    from report_engine.humanize import human_metric_name

    assert human_metric_name("custom_producer_metric") == "custom_producer_metric"
    assert human_metric_name("bad*name_with|pipe") == "bad*name_with|pipe"


def test_humanize_render_report_reexports_are_the_same_objects():
    """Legacy callers still reference render_report.<name>; consolidation
    must preserve that path."""
    import render_report
    from report_engine import humanize

    assert render_report.METRIC_LABELS is humanize.METRIC_LABELS
    assert render_report.human_metric_name is humanize.human_metric_name
    assert render_report.display_label is humanize.display_label
    assert render_report.rule_label_parts is humanize.rule_label_parts
    assert render_report.stringify is humanize.stringify


def test_deltas_pct_delta_matches_baseline_helper():
    import baselines
    from report_engine import deltas

    assert deltas.pct_delta(150.0, 100.0) == baselines.pct_delta(150.0, 100.0)
    # Zero baseline clamps to 1.0, not a division error.
    assert deltas.pct_delta(7.0, 0.0) == 700.0


def test_deltas_direction_matches_baseline_helper():
    from report_engine import deltas

    assert deltas.direction(5.0) == "increase"
    assert deltas.direction(-3.0) == "decrease"
    assert deltas.direction(0.0) == "no_change"


def test_deltas_signed_delta_pp_is_subtraction_not_relative_change():
    """signed_delta_pp is the percentage-point delta for two values
    already expressed as percentages. It must NOT compute the relative
    pct_delta — that would conflate share-of-X with change-of-X."""
    from report_engine import deltas

    assert deltas.signed_delta_pp(42.5, 40.0) == 2.5
    assert deltas.signed_delta_pp(40.0, 42.5) == -2.5
    assert deltas.signed_delta_pp(0.0, 0.0) == 0.0
    # Negative inputs also work — caller is responsible for unit sanity.
    assert deltas.signed_delta_pp(-1.0, -3.0) == 2.0


def test_deltas_signed_delta_pp_returns_float_for_int_inputs():
    from report_engine import deltas

    result = deltas.signed_delta_pp(10, 7)
    assert result == 3.0
    assert isinstance(result, float)


# ---------------------------------------------------------------------------
# Companion selection (M1.2 extraction into report_engine.contexts._shared)
# ---------------------------------------------------------------------------


def _control_fixture(**overrides):
    """Minimal ``bot_control_review.v1`` artifact for companion-selection tests."""
    base = {
        "schema_version": "bot_control_review.v1",
        "artifact_id": "control-1",
        "before_window": {"start": "2026-04-08T00:00:00Z", "end": "2026-04-15T00:00:00Z"},
        "after_window": {"start": "2026-04-15T00:00:00Z", "end": "2026-04-22T00:00:00Z"},
        "scope": {"cluster": "demo", "database": "akamai"},
        "table_used": "akamai.bi_summary_hour",
        "comparison_type": "post_change_vs_expected",
        "target": {"feature": "policy-tighten-1"},
        "target_effects": [],
    }
    base.update(overrides)
    return base


def _posture_fixture(**overrides):
    base = {
        "schema_version": "bot_posture_movement.v1",
        "artifact_id": "posture-1",
        "scope": {"cluster": "demo", "database": "akamai"},
        "table_used": "akamai.bi_summary_hour",
        "comparison_type": "previous_window",
        "current_window": {
            "start": "2026-04-15T00:00:00Z",
            "end": "2026-04-22T00:00:00Z",
        },
        "baseline_windows": [
            {"start": "2026-04-08T00:00:00Z", "end": "2026-04-15T00:00:00Z"}
        ],
        "metrics": [],
    }
    base.update(overrides)
    return base


def _mover_fixture(**overrides):
    base = {
        "schema_version": "bot_mover_attribution.v1",
        "artifact_id": "mover-1",
        "scope": {"cluster": "demo", "database": "akamai"},
        "table_used": "akamai.bi_summary_hour",
        "comparison_type": "previous_window",
        "current_window": {
            "start": "2026-04-15T00:00:00Z",
            "end": "2026-04-22T00:00:00Z",
        },
        "baseline_windows": [
            {"start": "2026-04-08T00:00:00Z", "end": "2026-04-15T00:00:00Z"}
        ],
        "movers": [],
    }
    base.update(overrides)
    return base


def _timeseries_fixture(**overrides):
    base = {
        "schema_version": "bot_timeseries.v1",
        "artifact_id": "timeseries-1",
        "scope": {"cluster": "demo", "database": "akamai"},
        "table_used": "akamai.bi_summary_hour",
        "comparison_type": "previous_window",
        "current_window": {
            "start": "2026-04-15T00:00:00Z",
            "end": "2026-04-22T00:00:00Z",
        },
        "baseline_windows": [
            {"start": "2026-04-08T00:00:00Z", "end": "2026-04-15T00:00:00Z"}
        ],
        "metrics": [],
    }
    base.update(overrides)
    return base


def test_select_control_companions_happy_path_with_control_only():
    from report_engine.contexts._shared import select_control_companions

    warnings = []
    result = select_control_companions(
        [_control_fixture()],
        warn=warnings.append,
    )
    assert result["control"]["artifact_id"] == "control-1"
    assert result["posture"] is None
    assert result["mover"] is None
    assert result["timeseries"] is None
    assert warnings == []


def test_select_control_companions_drops_posture_when_window_metadata_differs():
    """The legacy renderer's compatibility check requires every field in
    COMPANION_COMPAT_FIELDS to match. The control artifact carries
    ``before_window``/``after_window``, not ``current_window``/
    ``baseline_windows``, so a posture companion is always rejected on
    missing-metadata grounds. Pin this behavior so the engine port matches.
    """
    from report_engine.contexts._shared import select_control_companions

    warnings = []
    result = select_control_companions(
        [_control_fixture(), _posture_fixture()],
        warn=warnings.append,
    )
    assert result["posture"] is None
    assert any(
        "posture posture-1" in w and "missing current_window" in w for w in warnings
    ), f"Expected missing-metadata warning, got: {warnings}"


def test_select_control_companions_accepts_companion_when_metadata_aligns():
    """If a companion happens to carry the same compatibility fields as
    the control (synthetic but possible), it should pass through."""
    from report_engine.contexts._shared import select_control_companions

    control = _control_fixture(
        current_window={
            "start": "2026-04-15T00:00:00Z",
            "end": "2026-04-22T00:00:00Z",
        },
        baseline_windows=[
            {"start": "2026-04-08T00:00:00Z", "end": "2026-04-15T00:00:00Z"}
        ],
        comparison_type="previous_window",
    )
    warnings = []
    result = select_control_companions(
        [control, _posture_fixture()],
        warn=warnings.append,
    )
    assert result["posture"]["artifact_id"] == "posture-1"
    assert warnings == []


def test_select_control_companions_raises_when_no_control_present():
    from report_engine.contexts._shared import select_control_companions

    with pytest.raises(ValueError, match="missing bot_control_review.v1"):
        select_control_companions([_posture_fixture()])


def test_select_control_companions_raises_on_multiple_controls():
    from report_engine.contexts._shared import select_control_companions

    with pytest.raises(ValueError, match="multiple bot_control_review.v1"):
        select_control_companions(
            [_control_fixture(), _control_fixture(artifact_id="control-2")]
        )


def test_select_control_companions_raises_on_multiple_postures():
    from report_engine.contexts._shared import select_control_companions

    with pytest.raises(ValueError, match="multiple bot_posture_movement.v1"):
        select_control_companions(
            [
                _control_fixture(),
                _posture_fixture(),
                _posture_fixture(artifact_id="posture-2"),
            ]
        )


def test_select_control_companions_drops_mover_with_conflicting_table_used():
    """Conflicting metadata (not just missing) also disqualifies a companion."""
    from report_engine.contexts._shared import select_control_companions

    control = _control_fixture(
        current_window={
            "start": "2026-04-15T00:00:00Z",
            "end": "2026-04-22T00:00:00Z",
        },
        baseline_windows=[
            {"start": "2026-04-08T00:00:00Z", "end": "2026-04-15T00:00:00Z"}
        ],
        comparison_type="previous_window",
    )
    bad_mover = _mover_fixture(table_used="akamai.bi_summary_day")
    warnings = []
    result = select_control_companions([control, bad_mover], warn=warnings.append)
    assert result["mover"] is None
    assert any("conflict on table_used" in w for w in warnings)


def test_select_control_companions_returns_timeseries_when_compatible():
    from report_engine.contexts._shared import select_control_companions

    control = _control_fixture(
        current_window={
            "start": "2026-04-15T00:00:00Z",
            "end": "2026-04-22T00:00:00Z",
        },
        baseline_windows=[
            {"start": "2026-04-08T00:00:00Z", "end": "2026-04-15T00:00:00Z"}
        ],
        comparison_type="previous_window",
    )
    warnings = []
    result = select_control_companions(
        [control, _timeseries_fixture()], warn=warnings.append
    )
    assert result["timeseries"]["artifact_id"] == "timeseries-1"
    assert warnings == []


def test_select_control_companions_warn_callable_is_optional():
    """``warn=None`` should suppress reporting; dropped companions still
    become ``None``. The legacy renderer always wires ``ctx.warn`` but
    tests and ad hoc callers should not have to."""
    from report_engine.contexts._shared import select_control_companions

    result = select_control_companions([_control_fixture(), _posture_fixture()])
    assert result["posture"] is None


def test_companion_compatible_known_helper_recognizes_empty_collections():
    """`known` is used to gate compatibility checks; empty containers are
    not "known" values and must disqualify the field on either side."""
    from report_engine.contexts._shared import known

    assert known("akamai.bi_summary_hour")
    assert known({"cluster": "demo"})
    assert known(["window-1"])
    assert known(0)
    assert known(False)
    assert not known(None)
    assert not known("")
    assert not known([])
    assert not known({})


# ---------------------------------------------------------------------------
# Control review — engine port (M1.2 part B)
# ---------------------------------------------------------------------------


def test_control_review_assemble_from_example_fixture():
    """Ported example wrapper assembles to the expected dict shape.

    The shipped example carries one control artifact, no companions, so
    posture/mover/timeseries should all be ``None``.
    """
    import json

    from report_engine.contexts import control_review

    wrapper = json.loads(
        (FIXTURES / "control_review_full.json").read_text()
    )
    result = control_review.assemble(wrapper["artifacts"])
    assert result["control"]["schema_version"] == "bot_control_review.v1"
    assert result["control"]["artifact_id"] == "control-review-1"
    assert result["posture"] is None
    assert result["mover"] is None
    assert result["timeseries"] is None


def test_control_review_prepare_emits_target_effects_rows():
    """``prepare()`` projects ``target_effects`` into the row shape the
    template consumes, with metric labels resolved through
    ``human_metric_name`` and status tones populated."""
    import json

    from report_engine.contexts import control_review

    wrapper = json.loads(
        (FIXTURES / "control_review_full.json").read_text()
    )
    artifact = control_review.assemble(wrapper["artifacts"])
    ctx = control_review.prepare(artifact)

    assert ctx["title"] == "Control Review"
    assert ctx["target"]["descriptor"] == "policy-bot-block-1"
    assert ctx["expected_basis"] == "explicit_target"
    assert ctx["expected_basis_label"] == "Explicit target"

    effects = ctx["effects"]
    assert len(effects) == 1
    effect = effects[0]
    assert effect["metric"] == "siem_blocked_requests"
    assert effect["metric_label"] == "SIEM blocked requests"
    assert effect["before"] == 90.0
    assert effect["after"] == 280.0
    assert effect["expected"] == 100.0
    assert effect["status"] == "increased"
    assert effect["status_label"] == "Increased"
    assert effect["status_tone"] == "monitor"
    assert effect["confidence"] == "high"


def test_control_review_prepare_emits_collateral_and_displacement_checks():
    """Collateral and displacement check arrays project to row dicts
    with the same status/tone shape the effects rows use."""
    import json

    from report_engine.contexts import control_review

    wrapper = json.loads(
        (FIXTURES / "control_review_full.json").read_text()
    )
    artifact = control_review.assemble(wrapper["artifacts"])
    ctx = control_review.prepare(artifact)

    coll = ctx["collateral_checks"]
    assert len(coll) == 1
    assert coll[0]["metric"] == "rate_429_pct"
    assert coll[0]["before"] == 0.4
    assert coll[0]["after"] == 2.1
    assert coll[0]["status"] == "increased"

    disp = ctx["displacement_checks"]
    assert len(disp) == 1
    assert disp[0]["metric"] == "requests"
    assert disp[0]["before"] == 1200000.0
    assert disp[0]["after"] == 1100000.0


def test_control_review_prepare_emits_dominant_finding_with_caveat():
    """The synthesized finding describes the dominant effect, calls out
    expected basis, and carries the no-causal-claim caveat."""
    import json

    from report_engine.contexts import control_review

    wrapper = json.loads(
        (FIXTURES / "control_review_full.json").read_text()
    )
    artifact = control_review.assemble(wrapper["artifacts"])
    ctx = control_review.prepare(artifact)

    assert len(ctx["findings"]) == 1
    finding = ctx["findings"][0]
    assert "SIEM blocked requests" in finding.headline
    assert "increased" in finding.headline
    assert "policy-bot-block-1" in finding.headline
    assert "explicit target" in (finding.body or "").lower()
    assert finding.recommendation is not None
    assert "external change evidence" in finding.recommendation.lower()
    assert finding.caveat is not None
    assert "causal" in finding.caveat.lower()


def test_control_review_prepare_empty_effects_emits_placeholder_finding():
    """An artifact with no ``target_effects`` still produces a finding
    so the executive summary slot has something to render."""
    from report_engine.contexts import control_review

    artifact = control_review.assemble(
        [
            {
                "schema_version": "bot_control_review.v1",
                "artifact_id": "control-empty-1",
                "before_window": {"start": "2026-04-08", "end": "2026-04-15"},
                "after_window": {"start": "2026-04-15", "end": "2026-04-22"},
                "scope": {"cluster": "demo"},
                "table_used": "demo.bi",
                "comparison_type": "post_change_vs_expected",
                "target": {"policy_id": "policy-x"},
                "target_effects": [],
            }
        ]
    )
    ctx = control_review.prepare(artifact)
    assert ctx["effects"] == []
    assert len(ctx["findings"]) == 1
    assert "No effects" in ctx["findings"][0].title


def test_control_review_renders_via_engine_with_oracle_class_names():
    """Smoke test the rendered HTML contains the engine-style class
    names the parity gates will assert on in M2.

    Renders through ``uv run`` (the same path the other snapshot tests
    use) so jinja2 doesn't have to be importable from the local Python.
    """
    wrapper = FIXTURES / "control_review_full.json"
    snapshot = SNAPSHOTS / "control_review_full.html"
    actual = _normalize(_render(wrapper))
    _assert_snapshot(actual, snapshot)

    # Engine-style scaffolding that the parity gates and class-presence
    # audit (M4.5) will assert on. These are inline assertions on top of
    # the snapshot comparison so the test's intent is legible.
    for needle in (
        "narrative-slot",
        "exec-summary",
        "report-header",
        "purpose-strip",
        "control-target",
        "control-effects",
        "control-collateral",
        "control-displacement",
        "effects-table",
        "status-pill",
    ):
        assert needle in actual, (
            f"expected class fragment {needle!r} in control_review render"
        )

    assert "SIEM blocked requests" in actual
    assert "Adjacent populations" in actual
    assert "substitute paths" in actual
    assert "Increased" in actual


def test_control_review_target_descriptor_falls_back_to_key_value_join():
    """When the target dict carries an unfamiliar identifier shape, the
    descriptor falls back to a deterministic ``key=value`` join so the
    headline never collapses to empty.

    Uses ``prepare()`` directly because this assertion is about context
    shape, not rendered HTML — keeps it runnable from a plain Python
    without the uv dependency.
    """
    from report_engine.contexts import control_review

    artifact = control_review.assemble(
        [
            {
                "schema_version": "bot_control_review.v1",
                "artifact_id": "control-target-fallback-1",
                "before_window": {"start": "2026-04-08", "end": "2026-04-15"},
                "after_window": {"start": "2026-04-15", "end": "2026-04-22"},
                "scope": {"cluster": "demo"},
                "table_used": "demo.bi",
                "comparison_type": "post_change_vs_expected",
                "target": {"custom_key": "custom-value", "other": "v"},
                "target_effects": [],
            }
        ]
    )
    ctx = control_review.prepare(artifact)
    # Sorted ``key=value`` join keeps the output deterministic.
    assert ctx["target"]["descriptor"] == "custom_key=custom-value, other=v"


def test_companion_compatible_returns_reason_for_each_failure_mode():
    from report_engine.contexts._shared import companion_compatible

    # Primary that aligns on every COMPANION_COMPAT_FIELDS entry with a
    # baseline posture, so that we can construct failure scenarios by
    # toggling exactly one field at a time.
    base_window = {"start": "2026-04-15T00:00:00Z", "end": "2026-04-22T00:00:00Z"}
    base_prior = {"start": "2026-04-08T00:00:00Z", "end": "2026-04-15T00:00:00Z"}
    primary = _control_fixture(
        current_window=base_window,
        baseline_windows=[base_prior],
        comparison_type="previous_window",
    )

    ok, reason = companion_compatible(None, _posture_fixture())
    assert not ok
    assert "no primary artifact" in reason

    missing = _posture_fixture(
        current_window=base_window,
        baseline_windows=[base_prior],
        comparison_type="previous_window",
        scope={},
    )
    ok, reason = companion_compatible(primary, missing)
    assert not ok
    assert "missing scope" in reason

    conflicting = _posture_fixture(
        current_window=base_window,
        baseline_windows=[base_prior],
        comparison_type="rolling_baseline",
    )
    ok, reason = companion_compatible(primary, conflicting)
    assert not ok
    assert "conflict on comparison_type" in reason
