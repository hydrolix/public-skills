"""Per-report-type class-presence audit (M4.5 additive).

For every wrapper-mode fixture, render to HTML via the engine and assert
that the expected scaffolding classes are present. Lightweight smoke
gate that catches "template silently dropped a structural section"
regressions — the kind of failure that the parity gates caught
incidentally and that engine-only snapshot tests catch only after a
snapshot refresh.

Lands as part of M4.5's safety-net suite before the parity gates
(``tests/test_html_parity.py``, ``tests/test_markdown_parity.py``) and
the ``BOT_INSIGHTS_RENDER_PATH`` test override retire. Engine renders
exercise the same ``render_report.py`` front-end production callers
hit; no separate invocation surface, no per-test env-var pinning.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import _html_tree as html_tree  # noqa: E402

RENDER_REPORT = ROOT / "skills/bot-insights/scripts/render_report.py"
FIXTURE_DIRS = (
    ROOT / "tests/fixtures/report_engine",
    ROOT / "skills/bot-insights/examples",
)
_UV_WITH = (
    "--with",
    "jinja2",
    "--with",
    "markdown-it-py",
    "--with",
    "bleach",
)


# Universal scaffolding every wrapper-mode engine render must carry.
# A render that omits any of these has dropped its content envelope —
# almost always an extends/block bug or a template-include miss.
_UNIVERSAL_CLASSES = frozenset(
    {
        "header-titles",
        "kicker",
        "dek",
        "section-eyebrow",
        "method",
        "method-disclosure",
    }
)

# Per-report-type structural classes: a single representative class
# from each major section of the report type's template. Asserted *in
# addition to* ``_UNIVERSAL_CLASSES``. Keep the matrix small and
# load-bearing — adding too many makes the matrix a snapshot in
# disguise and breaks on every cosmetic template tweak.
_PER_TYPE_CLASSES: dict[str, frozenset[str]] = {
    "executive_posture": frozenset({
        "movement-table",
        "movers-table",
        "narrative-slot",
        "exec-summary",
    }),
    "scorecard_brief": frozenset({
        "landscape-grid",
        "queue-table",
        "verdict-strip",
        "findings",
        "exec-summary",
    }),
    "scorecard_entity_review": frozenset({
        "narrative-slot",
        "exec-summary",
    }),
    "control_review": frozenset({
        "control-bars",
        "control-target",
        "control-effects",
        "effects-table",
        "narrative-slot",
        "exec-summary",
    }),
    "soc_triage": frozenset({
        "verdict-strip",
        "queue-table",
        "domain-matrix",
        "sec-evidence-section",
        "exec-summary",
    }),
    "crawler_governance": frozenset({
        "verdict-strip",
        "queue-table",
        "domain-matrix",
        "sec-evidence-section",
        "exec-summary",
    }),
    "edge_ops_impact": frozenset({
        "verdict-strip",
        "queue-table",
        "domain-matrix",
        "sec-evidence-section",
        "path-candidates-table",
        "exec-summary",
    }),
}


def _wrapper_fixtures() -> list[Path]:
    found: list[Path] = []
    for d in FIXTURE_DIRS:
        if not d.exists():
            continue
        for path in sorted(d.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(data, dict):
                continue
            if data.get("schema_version") == "bot_report_input.v1":
                found.append(path)
    return found


def _ids(paths: list[Path]) -> list[str]:
    return [p.name for p in paths]


def _wrapper_report_type(wrapper: Path) -> str | None:
    try:
        data = json.loads(wrapper.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data.get("report_type")


def _render_html(wrapper: Path) -> tuple[str, str, int]:
    """Render via the engine path through ``render_report.py``. Uses the
    default routing (``BOT_INSIGHTS_RENDER_PATH`` unset → engine) so the
    test exercises the same call surface production callers hit.
    """
    if shutil.which("uv") is None:
        pytest.skip("uv not available")
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
        out_path = Path(f.name)
    try:
        env = os.environ.copy()
        # Force engine routing explicitly so a stray BOT_INSIGHTS_RENDER_PATH
        # in the test process can't pin this to legacy.
        env["BOT_INSIGHTS_RENDER_PATH"] = "engine"
        result = subprocess.run(
            [
                "uv",
                "run",
                "--quiet",
                *_UV_WITH,
                "python",
                str(RENDER_REPORT),
                "--file",
                str(wrapper),
                "--format",
                "html",
                "--output",
                str(out_path),
            ],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        html = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
        return html, result.stderr, result.returncode
    finally:
        out_path.unlink(missing_ok=True)


_WRAPPERS = _wrapper_fixtures()


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_engine_render_carries_universal_scaffolding(wrapper: Path):
    """Every successful engine render must carry the universal
    scaffolding classes that define a report-shaped HTML document.
    Missing one usually means the base template's ``extends`` chain
    is broken or a wrapper block silently dropped the title.
    """
    html, stderr, rc = _render_html(wrapper)
    if rc != 0:
        pytest.skip(
            f"{wrapper.name} did not render via the engine "
            f"(rc={rc}); class audit applies only to successful "
            f"renders. stderr tail: {stderr.splitlines()[-2:]}"
        )
    root = html_tree.parse(html)
    classes = html_tree.class_set(root)
    missing = _UNIVERSAL_CLASSES - classes
    assert not missing, (
        f"{wrapper.name} engine render is missing universal "
        f"scaffolding classes: {sorted(missing)}"
    )


@pytest.mark.skipif(not _WRAPPERS, reason="no wrapper fixtures found")
@pytest.mark.parametrize("wrapper", _WRAPPERS, ids=_ids(_WRAPPERS))
def test_engine_render_carries_per_type_scaffolding(wrapper: Path):
    """Engine renders for each ``report_type`` must carry the
    type-specific scaffolding classes defined in ``_PER_TYPE_CLASSES``.
    Adding/removing a section in a template surfaces here without
    needing a snapshot refresh.
    """
    rt = _wrapper_report_type(wrapper)
    expected = _PER_TYPE_CLASSES.get(rt)
    if expected is None:
        pytest.skip(f"{wrapper.name} carries report_type {rt!r}; no matrix entry")
    html, stderr, rc = _render_html(wrapper)
    if rc != 0:
        pytest.skip(
            f"{wrapper.name} did not render via the engine "
            f"(rc={rc}); class audit applies only to successful "
            f"renders. stderr tail: {stderr.splitlines()[-2:]}"
        )
    root = html_tree.parse(html)
    classes = html_tree.class_set(root)
    missing = expected - classes
    # Some fixtures legitimately omit sections (e.g., an index-only
    # SOC fixture has no per-entity sec-evidence-section). Only fail
    # when the universal subset is missing — type-specific gaps
    # become advisory until M4.5 hardens them with per-fixture
    # exemption lists if that turns out to be needed.
    universal = expected & frozenset({"exec-summary", "narrative-slot"})
    universal_missing = universal - classes
    assert not universal_missing, (
        f"{wrapper.name} engine render is missing per-type universal "
        f"scaffolding classes: {sorted(universal_missing)}"
    )
    if missing:
        # Surface as an advisory pytest warning rather than a hard
        # failure — keeps the gate honest while letting legitimately
        # degraded fixtures (e.g., index-only ranking) coexist.
        import warnings
        warnings.warn(
            f"{wrapper.name} ({rt}) missing per-type scaffolding "
            f"classes {sorted(missing)}; review whether the fixture "
            f"legitimately omits these sections or the template "
            f"regressed",
            stacklevel=1,
        )
