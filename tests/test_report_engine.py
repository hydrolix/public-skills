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
